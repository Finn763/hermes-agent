"""DingTalk proactive delivery regressions (#40818).

Cron ``deliver=dingtalk`` and ``send_message(target="dingtalk")`` always fail
once the ~2h ``session_webhook`` cache expires: the tool bypasses the live
gateway adapter, and the adapter itself hard-errors instead of falling back
to the configured static robot webhook.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from tools.send_message_tool import _send_to_platform


def test_dingtalk_send_to_platform_routes_through_send_via_adapter(monkeypatch):
    """DingTalk text sends go through _send_via_adapter (live adapter first)."""
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)

    live_send = AsyncMock(return_value={"success": True, "message_id": "live-id"})

    with patch("tools.send_message_tool._send_via_adapter", live_send):
        result = asyncio.run(
            _send_to_platform(
                Platform.DINGTALK,
                SimpleNamespace(enabled=True, extra={}),
                "cidABC==",
                "cron alert",
            )
        )

    assert result == {"success": True, "message_id": "live-id"}
    live_send.assert_awaited_once()
    call = live_send.await_args
    assert call.args[0] == Platform.DINGTALK
    assert call.args[2] == "cidABC=="


@pytest.mark.asyncio
async def test_adapter_send_falls_back_to_static_webhook(monkeypatch):
    """No cached session_webhook + static webhook configured -> POST text."""
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
    from plugins.platforms.dingtalk.adapter import DingTalkAdapter

    adapter = DingTalkAdapter(
        PlatformConfig(enabled=True, extra={"webhook_url": "https://hook.example/static"})
    )
    assert adapter._session_webhooks == {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    adapter._http_client = mock_client

    result = await adapter.send("cidABC==", "cron alert")

    assert result.success is True
    mock_client.post.assert_awaited_once()
    call = mock_client.post.await_args
    assert call.args[0] == "https://hook.example/static"
    assert call.kwargs["json"] == {"msgtype": "text", "text": {"content": "cron alert"}}


@pytest.mark.asyncio
async def test_adapter_send_still_errors_without_any_webhook(monkeypatch):
    """No session cache and no static webhook -> the old descriptive error."""
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
    from plugins.platforms.dingtalk.adapter import DingTalkAdapter

    adapter = DingTalkAdapter(PlatformConfig(enabled=True, extra={}))
    adapter._http_client = AsyncMock()

    result = await adapter.send("cidABC==", "cron alert")

    assert result.success is False
    assert "session_webhook" in result.error


def test_platform_hints_has_dingtalk():
    from agent.prompt_builder import PLATFORM_HINTS

    assert "dingtalk" in PLATFORM_HINTS
    assert PLATFORM_HINTS["dingtalk"].strip() != ""
