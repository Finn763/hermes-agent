"""RED test for issue #36715: approval card update must reach all members.

Calls _resolve_approval with a test double for the Feishu update API and
asserts the resolved card is pushed via PUT im/v1/messages (global update),
not just the clicker-only callback response.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _ensure_feishu_mocks():
    if importlib.util.find_spec("lark_oapi") is None and "lark_oapi" not in sys.modules:
        mod = MagicMock()
        for name in (
            "lark_oapi",
            "lark_oapi.api.im.v1",
            "lark_oapi.event",
            "lark_oapi.event.callback_type",
        ):
            sys.modules.setdefault(name, mod)
    if importlib.util.find_spec("aiohttp") is None and "aiohttp" not in sys.modules:
        aio = MagicMock()
        sys.modules.setdefault("aiohttp", aio)
        sys.modules.setdefault("aiohttp.web", aio.web)


_ensure_feishu_mocks()

from gateway.config import PlatformConfig
from plugins.platforms.feishu.adapter import FeishuAdapter


def _make_adapter() -> FeishuAdapter:
    config = PlatformConfig(enabled=True)
    adapter = FeishuAdapter(config)
    adapter._client = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_resolve_approval_updates_card_globally():
    adapter = _make_adapter()
    adapter._approval_state[7] = {
        "session_key": "sess-global-7",
        "message_id": "msg_global_7",
        "chat_id": "oc_12345",
    }
    with (
        patch("tools.approval.resolve_gateway_approval", return_value=1),
        patch.object(adapter, "_run_blocking", new_callable=AsyncMock) as mock_run,
    ):
        await adapter._resolve_approval(
            7, "once", "Bob", open_id="ou_bob", chat_id="oc_12345"
        )
    assert mock_run.call_count == 1, "expected one global card update via message.update"
    func, request = mock_run.call_args[0][0], mock_run.call_args[0][1]
    assert func == adapter._client.im.v1.message.update
    assert getattr(request, "message_id", "") == "msg_global_7"
    body = getattr(request, "request_body", None)
    assert body is not None
    assert getattr(body, "msg_type", "") == "interactive"
    card = json.loads(getattr(body, "content", "{}"))
    assert "Bob" in card["elements"][0]["content"]
    assert 7 not in adapter._approval_state
