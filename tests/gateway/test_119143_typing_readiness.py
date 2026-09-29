"""RED test for #119143: typing indicator must not fire during inbound
enrichment (STT/transcription) — only once the agent run starts.

Protocol (reuses the existing _typing_paused primitive):
- adapter gates typing at spawn via pause_typing_for_chat()
- runner signals readiness via resume_typing_for_chat() right before _run_agent
- early returns never signal -> zero typing
"""

import asyncio
import time

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    SendResult,
)
from gateway.session import SessionSource, build_session_key


class _Adapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="fake"), Platform.TELEGRAM)
        self._busy_text_mode = ""
        self.typing_ts = []

    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="1")

    async def send_typing(self, chat_id, metadata=None):
        self.typing_ts.append(time.monotonic())
        return None

    async def stop_typing(self, chat_id, metadata=None):
        return None

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


def _event(chat_id="c1"):
    return MessageEvent(
        text="voice note",
        source=SessionSource(
            platform=Platform.TELEGRAM, chat_id=chat_id, chat_type="private"
        ),
        message_id="m1",
    )


@pytest.mark.asyncio
async def test_typing_gated_until_agent_run():
    adapter = _Adapter()
    marks = {}

    async def handler(event):
        marks["pre_start"] = time.monotonic()
        await asyncio.sleep(2.5)  # inbound STT / transcription window
        marks["pre_end"] = time.monotonic()
        # runner protocol: readiness immediately before the agent run
        adapter.resume_typing_for_chat(event.source.chat_id)
        marks["ready"] = time.monotonic()
        await asyncio.sleep(4.5)  # agent run window (>2 typing intervals)
        return "done"

    adapter.set_message_handler(handler)
    event = _event()
    await adapter._process_message_background(event, build_session_key(event.source))

    pre = [t for t in adapter.typing_ts if t < marks["ready"]]
    post = [t for t in adapter.typing_ts if t >= marks["ready"]]
    assert marks["pre_end"] - marks["pre_start"] >= 2.0, "STT window too short"
    assert pre == [], f"typing fired during inbound enrichment: {len(pre)} calls"
    assert len(post) >= 1, "no typing after agent run started"
