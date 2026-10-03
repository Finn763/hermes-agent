"""DingTalk stream-mode send_message routing regression (#11966).

In pure stream mode no static DINGTALK_WEBHOOK_URL exists, so routing every
send straight to the registry standalone sender fails with "DingTalk not
configured" even while the live stream adapter holds a valid cached
session_webhook. Sends must try the live adapter first (like Slack).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.config import Platform
from tools.send_message_tool import _send_to_platform


def test_dingtalk_send_to_platform_prefers_live_adapter(monkeypatch):
    """DingTalk text sends go through _send_via_adapter (live adapter first)."""
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)

    live_send = AsyncMock(return_value={"success": True, "message_id": "live-id"})
    standalone_send = AsyncMock(
        return_value={"error": "DingTalk not configured. Set DINGTALK_WEBHOOK_URL env var."}
    )

    with (
        patch("tools.send_message_tool._send_via_adapter", live_send),
        patch("tools.send_message_tool._registry_standalone_send", standalone_send),
    ):
        result = asyncio.run(
            _send_to_platform(
                Platform.DINGTALK,
                SimpleNamespace(enabled=True, token="", extra={}),
                "cidABC==",
                "hello from Hermes",
            )
        )

    assert live_send.await_count == 1
    assert standalone_send.await_count == 0
    assert result.get("success") is True


def test_dingtalk_send_falls_back_to_standalone_when_live_fails(monkeypatch):
    """No cached session_webhook -> standalone static webhook still delivers."""
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)

    live_send = AsyncMock(return_value={"error": "Adapter send failed: no webhook"})
    standalone_send = AsyncMock(
        return_value={"success": True, "platform": "dingtalk", "chat_id": "cidABC=="}
    )

    with (
        patch("tools.send_message_tool._send_via_adapter", live_send),
        patch("tools.send_message_tool._registry_standalone_send", standalone_send),
    ):
        result = asyncio.run(
            _send_to_platform(
                Platform.DINGTALK,
                SimpleNamespace(enabled=True, token="", extra={}),
                "cidABC==",
                "hello from Hermes",
            )
        )

    assert live_send.await_count == 1
    assert standalone_send.await_count == 1
    assert result.get("success") is True
