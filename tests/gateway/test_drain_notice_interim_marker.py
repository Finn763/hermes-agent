"""Busy-path sends must carry the interim marker at the delivery rail.

The busy helper backs three mid-turn lanes (busy-ack, plaintext-approval fallback,
shutdown/restart drain notice). All fire while turns still stream, so on a
stream-is-the-message adapter an unmarked send seals the in-flight answer with
this text -- the #98432 contract class. Harness helpers are reused from the
busy-ack suite.
"""

import pytest  # pyright: ignore[reportMissingImports]

from gateway.platforms.base import build_session_key
from tests.gateway.test_busy_session_ack import (
    _make_adapter,
    _make_event,
    _make_runner,
)


@pytest.mark.asyncio
async def test_drain_notice_carries_interim_marker_at_send():
    runner, _sentinel = _make_runner()
    runner._restart_requested = True
    adapter = _make_adapter()

    event = _make_event(text="one more thing")
    sk = build_session_key(event.source)
    runner.adapters[event.source.platform] = adapter

    await runner._send_busy_drain_notice(event, sk, "interrupt")

    adapter._send_with_retry.assert_called_once()
    metadata = adapter._send_with_retry.call_args.kwargs.get("metadata") or {}
    assert metadata.get("_interim_send") is True


@pytest.mark.asyncio
async def test_busy_ack_carries_interim_marker_at_send():
    """The shared busy helper stamps the marker, covering the busy-ack lane."""
    runner, _sentinel = _make_runner()
    adapter = _make_adapter()

    event = _make_event(text="one more thing")

    await runner._send_busy_ack_reply(event, adapter, "⏳ Working on it")

    adapter._send_with_retry.assert_called_once()
    metadata = adapter._send_with_retry.call_args.kwargs.get("metadata") or {}
    assert metadata.get("_interim_send") is True
