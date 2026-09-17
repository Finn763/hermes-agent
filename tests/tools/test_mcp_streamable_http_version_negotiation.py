"""Streamable HTTP: the client must speak the revision ``initialize`` negotiated.

Per the MCP spec the ``MCP-Protocol-Version`` header belongs to the requests that
come *after* ``initialize``, and its value is the revision the server negotiated.
Hermes seeded that header from its own newest handshake revision *before* the
handshake ran, so a server that supports only an older revision answered
``400 -32020 Unsupported MCP-Protocol-Version`` to the very request whose body
was offering to negotiate — while a raw curl of the same handshake (which sends
no such header) succeeded.

The fake server below is the repo's usual local-socket fixture: it records every
request it sees and enforces the server side of the spec, so the assertions run
against real HTTP bytes and the real SDK session, not a mock.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import threading
from contextlib import contextmanager

import pytest

from tools import mcp_tool
from tools.mcp_tool import MCPServerTask


def _handshake_version() -> str:
    """The revision the SDK's ``initialize`` offers, read after the lazy SDK import.

    ``tools.mcp_tool.LATEST_HANDSHAKE_VERSION`` starts at the pre-import fallback
    (``2025-03-26``) and is replaced with the SDK's value on first use, so it must be
    read at call time, not captured at import time.
    """
    mcp_tool._ensure_mcp_sdk()
    return mcp_tool.LATEST_HANDSHAKE_VERSION

# The revision the fake server supports: older than anything Hermes offers, so the
# server counter-offers it and every later request has to speak it.
SERVER_SUPPORTED = "2025-03-26"


class _NegotiatingMCPServer:
    """Minimal Streamable HTTP MCP endpoint pinned to ``SERVER_SUPPORTED``.

    Rejects any request whose ``MCP-Protocol-Version`` header is present and is not
    the negotiated revision (as obsidian-mcp-connector does), and requires the
    negotiated revision on every request after ``initialize``.
    """

    def __init__(self) -> None:
        self.negotiated = SERVER_SUPPORTED
        self.records: list[tuple[str, dict, dict]] = []
        self._httpd = None

    # -- server side ------------------------------------------------------
    def _send(self, handler: http.server.BaseHTTPRequestHandler, code: int,
              payload, *, content_type: str | None = "application/json") -> None:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if code == 202:
            content_type, raw = None, b""
        handler.send_response(code)
        if content_type:
            handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        if raw:
            handler.wfile.write(raw)

    def _reject(self, handler, message_id, version: str | None) -> None:
        self._send(handler, 400, {"jsonrpc": "2.0", "id": message_id, "error": {
            "code": -32020, "message": f"Unsupported MCP-Protocol-Version: {version}"}})

    def _handler_class(self):
        server = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence the stdlib access log
                pass

            def do_GET(self):  # no server-initiated stream in this fixture
                server.records.append(("GET", dict(self.headers), {}))
                server._send(self, 405, {"error": "no server stream"})

            def do_DELETE(self):
                server.records.append(("DELETE", dict(self.headers), {}))
                server._send(self, 405, {"error": "no session to terminate"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    message = json.loads(raw)
                except ValueError:
                    message = {"_raw": raw.decode("utf-8", "replace")}
                headers = {k.lower(): v for k, v in self.headers.items()}
                server.records.append((str(message.get("method")), headers, message))
                sent = self.headers.get("MCP-Protocol-Version")
                if message.get("method") == "initialize":
                    # The handshake is where the version is agreed, so the header — if the
                    # client sends one at all — must not name a revision this server lacks.
                    if sent is not None and sent != server.negotiated:
                        server._reject(self, message.get("id"), sent)
                        return
                    server._send(self, 200, {"jsonrpc": "2.0", "id": message.get("id"), "result": {
                        "protocolVersion": server.negotiated,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "negotiating-fake", "version": "1.0"}}})
                    return
                if sent != server.negotiated:
                    server._reject(self, message.get("id"), sent)
                    return
                if message.get("method") == "notifications/initialized":
                    server._send(self, 202, b"")
                    return
                server._send(self, 200, {"jsonrpc": "2.0", "id": message.get("id"),
                                         "result": {"tools": []}})

        return _Handler

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self._httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), self._handler_class())
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/mcp"

    def versions_by_method(self) -> dict[str, str | None]:
        return {method: headers.get("mcp-protocol-version")
                for method, headers, _body in self.records}


@contextmanager
def _negotiating_server():
    server = _NegotiatingMCPServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _connect(server: _NegotiatingMCPServer, headers: dict | None = None) -> str:
    """Run Hermes's real Streamable HTTP bring-up against *server*; return the serve reason."""
    task = MCPServerTask("obsidian-vault")

    async def _stop_after_connect():  # instance attribute: no ``self`` binding
        return "shutdown"

    task._wait_for_lifecycle_event = _stop_after_connect
    config: dict = {"url": server.url}
    if headers:
        config["headers"] = headers
    return asyncio.run(task._run_http(config))


@pytest.fixture
def server():
    with _negotiating_server() as srv:
        yield srv


def test_a_server_negotiating_an_older_revision_connects(server):
    """The whole point of the issue: this connect used to fail, curl or no curl."""
    assert _handshake_version() != server.negotiated, "premise: the server is older than our offer"

    assert _connect(server) == "shutdown"

    methods = [method for method, _h, _b in server.records]
    assert methods[0] == "initialize"
    assert "notifications/initialized" in methods
    assert "tools/list" in methods


def test_the_initialize_request_does_not_advertise_an_unguessed_version(server):
    """No header before the handshake: the version is unknown until the server answers."""
    assert _handshake_version() != server.negotiated, "premise: the server is older than our offer"

    _connect(server)

    sent = server.versions_by_method()["initialize"]
    assert sent is None, (
        "the initialize request advertised a protocol version before the server had a say; "
        f"a server that only speaks {server.negotiated} rejects that request outright (got {sent!r})"
    )


def test_the_negotiated_revision_rides_every_request_after_the_handshake(server):
    """Accepting the counter-offer means *using* it, not just tolerating it."""
    assert _handshake_version() != server.negotiated, "premise: the server is older than our offer"

    _connect(server)

    after = {method: sent for method, sent in server.versions_by_method().items()
             if method != "initialize" and method in ("notifications/initialized", "tools/list")}
    assert after == {"notifications/initialized": server.negotiated, "tools/list": server.negotiated}


def test_the_initialize_body_still_offers_the_newest_handshake_revision(server):
    """We accept the server's lower counter-offer instead of pre-emptively lowering our own."""
    _connect(server)

    body = next(body for method, _h, body in server.records if method == "initialize")
    assert body["params"]["protocolVersion"] == _handshake_version()
