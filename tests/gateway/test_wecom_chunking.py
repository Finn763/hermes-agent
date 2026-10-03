"""RED tests for issue #25060: long cron output pushed to WeCom is lost.

WeCom truncates to 4000 chars in send() and fires chunked standalone sends
with no pacing, so long cron results hit the platform rate limit and arrive
incomplete. The adapter must split natively with inter-chunk pacing.
Uses test doubles only — no network, no credentials.
"""

import asyncio
import re
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.wecom.adapter import WeComAdapter


def _make_adapter(**extra):
    cfg_extra = {"bot_id": "b", "secret": "s"}
    cfg_extra.update(extra)
    adapter = WeComAdapter(PlatformConfig(enabled=True, extra=cfg_extra))
    adapter._send_chunk_delay_seconds = 0  # fast unit test; pacing covered separately
    return adapter


def _ok_response(req_id):
    return {"errcode": 0, "headers": {"req_id": req_id}}


@pytest.mark.asyncio
async def test_send_preserves_full_long_message():
    adapter = _make_adapter()
    content = "A" * 9000
    sent = []

    async def fake_send_request(cmd, body, timeout=15.0):
        sent.append(body["markdown"]["content"])
        return _ok_response(f"req-{len(sent)}")

    adapter._send_request = fake_send_request
    result = await adapter.send(chat_id="chat-1", content=content)

    assert result.success is True
    assert len(sent) > 1, "long content must be split, not truncated to one send"
    for chunk in sent:
        assert len(chunk) <= WeComAdapter.MAX_MESSAGE_LENGTH
    assert "".join(
        re.sub(r" \(\d+/\d+\)", "", chunk).replace("```", "").replace("\n", "")
        for chunk in sent
    ) == content


@pytest.mark.asyncio
async def test_send_paces_chunks_with_delay():
    adapter = _make_adapter()
    adapter._send_chunk_delay_seconds = 1.5
    adapter._send_request = AsyncMock(side_effect=[
        _ok_response("req-1"),
        _ok_response("req-2"),
        _ok_response("req-3"),
    ])
    with patch(
        "plugins.platforms.wecom.adapter.asyncio.sleep", new=AsyncMock()
    ) as sleep_mock:
        result = await adapter.send(chat_id="chat-1", content="B" * 9000)

    assert result.success is True
    assert adapter._send_request.await_count == 3
    # paced between chunks, never after the last one
    assert sleep_mock.await_count == 2
    sleep_mock.assert_any_await(1.5)


@pytest.mark.asyncio
async def test_send_reports_partial_progress_on_chunk_failure():
    adapter = _make_adapter()
    adapter._send_request = AsyncMock(side_effect=[
        _ok_response("req-1"),
        {"errcode": 44004, "errmsg": "rate limited"},
        _ok_response("req-3"),
    ])
    result = await adapter.send(chat_id="chat-1", content="C" * 9000)

    assert result.success is False
    assert "2/3" in (result.error or ""), result.error
    assert "1 delivered" in (result.error or ""), result.error


def test_adapter_declares_native_chunking():
    # gateway/delivery.py truncates cron output for adapters without this flag
    assert WeComAdapter.splits_long_messages is True
