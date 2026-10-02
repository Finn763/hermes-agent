"""RED test for #31346: SSE-only server (Cloud Run) behind default HTTP transport.

An SSE-only MCP server answers the StreamableHTTP ``initialize`` POST with
202 "accepted" and posts the result on the SSE stream, so the StreamableHTTP
client never sees an inline response and ``_negotiate_session``'s
``wait_for(connect_timeout)`` expires. Before the fix, ``run()`` burned the
backoff ladder on the wrong protocol and surfaced an opaque
``CancelledError`` (outer 120s discovery timeout); after the fix it retries
once via the SSE transport and connects.

Drives the real ``run()`` -> ``_run_http`` -> ``_negotiate_session`` path:
the fake StreamableHTTP session hangs in ``initialize()`` for real (genuine
``wait_for`` timeout machinery, short ``connect_timeout``), the fake SSE
session answers immediately. No real network.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tools.mcp_tool import MCPServerTask


class _FakeTransport:
    """Plain async CM yielding (read, write); never wraps errors in groups."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return (object(), object())

    async def __aexit__(self, *args):
        return False


class _HangingSession:
    """StreamableHTTP-side stand-in: initialize() never answers (202-accepted
    with the result posted on the SSE stream, per the #31346 diagnostics)."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def initialize(self):
        await asyncio.sleep(30.0)
        raise AssertionError("unreachable: wait_for must fire first")


class _FastSession:
    """SSE-side stand-in: initialize() answers immediately."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def initialize(self):
        return object()


def _make_client_session(hang_all: bool = False):
    calls = {"n": 0}

    def factory(*args, **kwargs):
        calls["n"] += 1
        if hang_all or calls["n"] == 1:
            return _HangingSession()
        return _FastSession()

    return factory


def _fake_streamable(*args, **kwargs):
    return _FakeTransport(None)


def _fake_sse_factory(seen):
    def fake_sse(*args, **kwargs):
        seen.append(kwargs)
        return _FakeTransport(None)

    return fake_sse


@pytest.mark.asyncio
async def test_streamable_handshake_timeout_falls_back_to_sse():
    server = MCPServerTask("cloudrun-probe")
    server._auth_type = ""
    server._sampling = None
    server._shutdown_event.set()  # break the run loop after first success
    config = {
        "url": "https://example.com/api/mcp",
        "connect_timeout": 0.3,
        "timeout": 5,
        "skip_preflight": True,
    }
    seen_transports: list = []
    real_run_http = MCPServerTask._run_http

    async def spy(self, cfg):
        seen_transports.append(cfg.get("transport", "http"))
        return await real_run_http(self, cfg)

    sse_seen: list = []
    with patch("tools.mcp_tool.streamable_http_client",
               new=_fake_streamable, create=True), \
         patch("tools.mcp_tool.streamablehttp_client",
               new=_fake_streamable, create=True), \
         patch("tools.mcp_tool.sse_client",
               new=_fake_sse_factory(sse_seen), create=True), \
         patch("tools.mcp_tool.ClientSession", new=_make_client_session()), \
         patch.object(MCPServerTask, "_run_http", new=spy), \
         patch.object(MCPServerTask, "_discover_tools", new=AsyncMock()), \
         patch.object(MCPServerTask, "_wait_for_lifecycle_event",
                       new=AsyncMock(return_value="shutdown")):
        await asyncio.wait_for(server.run(dict(config)), timeout=8.0)

    assert sse_seen, "expected one SSE-transport attempt after timeout"
    assert seen_transports == ["http", "sse"], seen_transports
    assert server._ready.is_set(), "server should be connected via SSE fallback"


@pytest.mark.asyncio
async def test_sse_fallback_is_single_shot():
    """If the SSE retry also times out, the original transport is restored
    and the normal backoff ladder resumes (no flip-flop, no sticky SSE)."""
    server = MCPServerTask("cloudrun-probe-2")
    server._auth_type = ""
    server._sampling = None
    # No shutdown preset: the loop must keep retrying on the restored
    # transport until our outer guard fires (proves no SSE hot-loop).
    config = {
        "url": "https://example.com/api/mcp",
        "connect_timeout": 0.2,
        "timeout": 5,
        "skip_preflight": True,
    }
    seen_transports: list = []
    real_run_http = MCPServerTask._run_http

    async def spy(self, cfg):
        seen_transports.append(cfg.get("transport", "http"))
        return await real_run_http(self, cfg)

    with patch("tools.mcp_tool.streamable_http_client",
               new=_fake_streamable, create=True), \
         patch("tools.mcp_tool.streamablehttp_client",
               new=_fake_streamable, create=True), \
         patch("tools.mcp_tool.sse_client", new=_fake_streamable, create=True), \
         patch("tools.mcp_tool.ClientSession",
               new=_make_client_session(hang_all=True)), \
         patch.object(MCPServerTask, "_run_http", new=spy), \
         patch.object(MCPServerTask, "_discover_tools", new=AsyncMock()), \
         patch.object(MCPServerTask, "_wait_for_lifecycle_event",
                       new=AsyncMock(return_value="shutdown")):
        # Both transports hang; the shutdown preset breaks the loop after
        # the post-restore backoff sleep. Bound it: must raise TimeoutError
        # from OUR guard (proving it kept retrying, not hot-looping SSE).
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(server.run(dict(config)), timeout=6.0)

    assert seen_transports[0] == "http"
    assert "sse" in seen_transports, seen_transports
    # After the failed SSE attempt the original transport is restored.
    assert seen_transports[-1] == "http", seen_transports
    assert seen_transports.count("sse") == 1, seen_transports
