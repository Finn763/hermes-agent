"""First-contact home-channel notices stay on the operator side of a shared gateway."""
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _runner(*, admins=()):
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.WHATSAPP: PlatformConfig(enabled=True, extra={"allow_admin_from": list(admins)})}
    )
    runner._deliver_platform_notice = AsyncMock()
    return runner


def _source(user_id="patient-1"):
    return SessionSource(platform=Platform.WHATSAPP, chat_id=user_id, chat_type="dm", user_id=user_id)


@pytest.mark.asyncio
async def test_untrusted_first_contact_does_not_receive_home_notice(monkeypatch):
    monkeypatch.delenv("WHATSAPP_HOME_CHANNEL", raising=False)
    runner = _runner()

    await runner._maybe_deliver_home_notice(_source())

    runner._deliver_platform_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_admin_receives_home_notice(monkeypatch):
    monkeypatch.delenv("WHATSAPP_HOME_CHANNEL", raising=False)
    runner = _runner(admins=("operator-1",))
    source = _source("operator-1")

    await runner._maybe_deliver_home_notice(source)

    runner._deliver_platform_notice.assert_awaited_once()
    assert "No home channel is set for Whatsapp" in runner._deliver_platform_notice.await_args.args[1]


@pytest.mark.asyncio
async def test_existing_home_channel_skips_notice_for_any_sender(monkeypatch):
    monkeypatch.setenv("WHATSAPP_HOME_CHANNEL", "operator-chat")
    runner = _runner()

    await runner._maybe_deliver_home_notice(_source())

    runner._deliver_platform_notice.assert_not_awaited()
