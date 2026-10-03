"""RED test for #33287: WeCom listener must not zombie-spin on a closed socket.

Root cause: ``_read_events`` returned normally when ``self._ws`` was already
closed on entry (``while ... not closed`` skipped). ``_listen_loop`` treated
that as success, reset backoff, and hot-spun forever without reconnecting --
the May-20 19:25 "closed during authentication" -> dead-for-4-hours shape.
Sibling: ``_send_json`` leaked raw "Cannot write to closing transport"
instead of the retryable not-connected error.
"""

import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import PlatformConfig


def _mk_adapter():
    from plugins.platforms.wecom.adapter import WeComAdapter

    return WeComAdapter(PlatformConfig(enabled=True, extra={"bot_id": "b", "secret": "s"}))


@pytest.mark.asyncio
async def test_read_events_raises_when_ws_already_closed():
    adapter = _mk_adapter()
    adapter._running = True
    adapter._ws = SimpleNamespace(closed=True)
    with pytest.raises(RuntimeError, match="[Cc]losed"):
        await adapter._read_events()


@pytest.mark.asyncio
async def test_send_json_maps_closing_transport_to_not_connected():
    adapter = _mk_adapter()

    class ClosingWS:
        closed = False

        async def send_json(self, payload):
            raise ConnectionResetError("Cannot write to closing transport")

    adapter._ws = ClosingWS()
    with pytest.raises(RuntimeError, match="not connected"):
        await adapter._send_json({"cmd": "ping"})


@pytest.mark.asyncio
async def test_listen_loop_reconnects_after_clean_read_return():
    from plugins.platforms.wecom import adapter as wecom_module

    adapter = _mk_adapter()
    adapter._running = True
    adapter._ws = SimpleNamespace(closed=True)

    calls = {"opens": 0}

    async def fake_read_events():
        return None

    async def fake_open():
        calls["opens"] += 1
        adapter._running = False

    adapter._read_events = fake_read_events  # type: ignore[method-assign]
    adapter._open_connection = fake_open  # type: ignore[method-assign]
    adapter._mark_connected = lambda: None  # type: ignore[method-assign]

    await asyncio.wait_for(
        adapter._listen_loop(), timeout=wecom_module.RECONNECT_BACKOFF[0] + 20
    )
    assert calls["opens"] >= 1, "clean _read_events return must trigger a reconnect"
