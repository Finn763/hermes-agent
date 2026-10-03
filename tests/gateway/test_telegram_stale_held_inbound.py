"""RED test for #22899: stale held inbound must not redispatch hours later.

Telegram hold queue preserves inbound events across disconnect (PTB already
acked the offset, so dropping = silent loss). But redispatching hours-old
events makes the agent work on stale state. Fresh events must still drain.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, SessionSource


def _make_adapter():
    from plugins.platforms.telegram.adapter import TelegramAdapter
    config = PlatformConfig(enabled=True, token="test-token")
    adapter = object.__new__(TelegramAdapter)
    adapter._platform = Platform.TELEGRAM
    adapter.platform = Platform.TELEGRAM
    adapter.config = config
    adapter._fatal_error_code = None
    adapter._fatal_error_retryable = True
    adapter._drop_delayed_deliveries = False
    adapter._held_inbound_events = []
    adapter._held_inbound_redispatch_task = None
    adapter.HELD_INBOUND_MAX = 64
    adapter.handle_message = AsyncMock()
    return adapter


def _make_event(text, age_seconds=None):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds) if age_seconds is not None else datetime.now(timezone.utc)
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm"),
        timestamp=ts,
    )


@pytest.mark.asyncio
async def test_stale_held_event_dropped_not_redispatched():
    adapter = _make_adapter()
    adapter._held_inbound_events = [_make_event("two-hours-old", age_seconds=7200)]
    await adapter._redispatch_held_inbound()
    adapter.handle_message.assert_not_called()
    assert adapter._held_inbound_events == []


@pytest.mark.asyncio
async def test_fresh_held_event_still_redispatched():
    adapter = _make_adapter()
    adapter._held_inbound_events = [_make_event("just-now", age_seconds=5)]
    await adapter._redispatch_held_inbound()
    adapter.handle_message.assert_called_once()
