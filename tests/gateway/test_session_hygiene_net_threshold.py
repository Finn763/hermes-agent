"""Session-hygiene net must never fire before the agent's own compressor (#118984).

The gateway's pre-agent hygiene net force-compacts at a ratio of the model window.
Until #118984 that ratio was hard-coded to 0.85, while the agent's own compressor
follows ``compression.threshold``. A user raising ``compression.threshold`` above
0.85 (supported, and the only way to give a large window a long runway) got the
net firing first at ``0.85 x window``: the session silently capped short of the
value the user asked for, and TUI/CLI sessions (which never pass through the net)
disagreed with gateway sessions on the very same config.

The invariant these tests pin: the net's trigger is never BELOW the agent's
trigger for the same config. When the user raises the ratio, the net follows;
when the user lowers it, the net keeps its own safety floor.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from gateway.run import GatewayRunner
from gateway.run_turn import GatewayTurnMixin
from gateway.config import Platform
from gateway.session import SessionEntry


@pytest.fixture
def runner():
    """A real GatewayRunner with no __init__ side effects (idiom from the sibling
    hygiene tests in this directory)."""
    r = object.__new__(GatewayRunner)
    r._session_db = None
    return r


def _hygiene_settings(runner, data):
    """Build the hygiene settings dataclass, then apply the user config onto it —
    the same two steps _hmwa_hygiene_settings performs before it resolves the model."""
    hs = runner._HygieneSettings(
        model="deepseek-v4-flash", threshold_pct=0.85, compression_enabled=True,
        hard_msg_limit=5000, timeout_seconds=30.0, total_ceiling_seconds=600.0,
        max_turn_hold_seconds=10.0, failure_cooldown_seconds=300.0,
        config_context_length=None, provider="deepseek",
        base_url="https://api.deepseek.com/v1", api_key="", data={},
    )
    GatewayTurnMixin._hmwa_hygiene_read_config(hs, data)
    return hs


def _session_entry(session_id="sess-net") -> SessionEntry:
    return SessionEntry(
        session_key="agent:main:telegram:private:12345",
        session_id=session_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="private",
    )


def _plan(runner, hs, history, tokens, monkeypatch):
    """Resolve the hygiene plan for a transcript whose real prompt usage is ``tokens``."""
    import agent.model_metadata as model_metadata

    async def _fake_ctx(*_args, **_kwargs):
        return 1_000_000

    # The plan imports it inside the body, so patch the defining module.
    monkeypatch.setattr(model_metadata, "get_model_context_length_async", _fake_ctx)
    entry = _session_entry()
    entry.last_prompt_tokens = tokens
    return asyncio.run(runner._hmwa_hygiene_plan(hs, history, entry, "key"))


def _history(n: int) -> list:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 200} for i in range(n)]


# ---------------------------------------------------------------------------
# The reported reproduction: a widened compression.threshold
# ---------------------------------------------------------------------------

def test_net_follows_compression_threshold_above_the_default(runner, monkeypatch):
    """compression.threshold: 0.95 moves the net with the agent (#118984)."""
    hs = _hygiene_settings(
        runner,
        {"compression": {"enabled": True, "threshold": 0.95, "threshold_tokens": 0}},
    )

    assert hs.threshold_pct == pytest.approx(0.95)
    assert int(1_000_000 * hs.threshold_pct) == 950_000


def test_net_does_not_fire_below_the_agents_own_trigger(runner, monkeypatch):
    """The net must not pre-empt a widened agent threshold.

    Reproduction config from the issue: a 1M catalogue window with
    compression.threshold 0.95 resolves the agent's trigger to 950,000. A session
    at 900,000 tokens is under the agent's trigger, so hygiene must stay quiet.
    """
    hs = _hygiene_settings(
        runner,
        {"compression": {"enabled": True, "threshold": 0.95, "threshold_tokens": 0}},
    )
    plan = _plan(runner, hs, _history(8), tokens=900_000, monkeypatch=monkeypatch)

    assert not plan.needs_compress, (
        "hygiene net fired at 900,000 tokens — below the agent's own 950,000 "
        "trigger for compression.threshold 0.95"
    )


def test_net_still_fires_above_the_agents_own_trigger(runner, monkeypatch):
    """Following the agent up must not disarm the net: a session that outgrew the
    widened trigger still gets caught before it hits the window."""
    hs = _hygiene_settings(
        runner,
        {"compression": {"enabled": True, "threshold": 0.95, "threshold_tokens": 0}},
    )
    plan = _plan(runner, hs, _history(8), tokens=960_000, monkeypatch=monkeypatch)

    assert plan.needs_compress


# ---------------------------------------------------------------------------
# The safety net itself must survive the fix
# ---------------------------------------------------------------------------

def test_net_keeps_its_safety_floor_when_the_agent_threshold_is_lower(runner, monkeypatch):
    """A lowered compression.threshold must not drag the net down with it.

    The net exists for sessions that grew between turns; its 0.85 floor stays
    regardless of how low the user sets the agent's ratio.
    """
    hs = _hygiene_settings(
        runner,
        {"compression": {"enabled": True, "threshold": 0.50, "threshold_tokens": 0}},
    )

    assert hs.threshold_pct == pytest.approx(0.85)


def test_net_still_arms_at_a_very_high_agent_threshold(runner, monkeypatch):
    """threshold 0.99 follows the user up, but never past the window: the trigger
    stays strictly below the model's context length so the session cannot ride
    into a provider 400 with compression never firing."""
    hs = _hygiene_settings(
        runner,
        {"compression": {"enabled": True, "threshold": 0.99, "threshold_tokens": 0}},
    )

    trigger = int(1_000_000 * hs.threshold_pct)
    assert hs.threshold_pct < 1.0, "the net trigger reached the whole window"
    assert trigger < 1_000_000
    plan = _plan(runner, hs, _history(8), tokens=995_000, monkeypatch=monkeypatch)
    assert plan.needs_compress


def test_net_keeps_the_hard_message_limit_regardless_of_threshold(runner, monkeypatch):
    """The message-count safety valve is independent of the ratio."""
    hs = _hygiene_settings(
        runner,
        {"compression": {"enabled": True, "threshold": 0.99, "threshold_tokens": 0}},
    )
    entry = _session_entry()
    entry.last_prompt_tokens = 10

    import agent.model_metadata as model_metadata

    async def _fake_ctx(*_args, **_kwargs):
        return 1_000_000

    monkeypatch.setattr(model_metadata, "get_model_context_length_async", _fake_ctx)
    plan = asyncio.run(runner._hmwa_hygiene_plan(hs, _history(5000), entry, "key"))

    assert plan.needs_compress, "hard message limit stopped forcing compression"


# ---------------------------------------------------------------------------
# Default behaviour must be untouched
# ---------------------------------------------------------------------------

def test_default_threshold_is_unchanged(runner):
    """No compression.threshold in the config: the net keeps its own 0.85."""
    hs = _hygiene_settings(runner, {"compression": {"enabled": True}})

    assert hs.threshold_pct == pytest.approx(0.85)


def test_invalid_threshold_falls_back_to_the_net_default(runner):
    """Garbage in the threshold key keeps the net's default instead of raising."""
    hs = _hygiene_settings(
        runner, {"compression": {"enabled": True, "threshold": "not-a-ratio"}},
    )

    assert hs.threshold_pct == pytest.approx(0.85)


def test_threshold_below_the_net_keeps_the_net_default(runner):
    """0.20 is a legitimate user value for the agent, but it must not pull the
    safety net down with it."""
    hs = _hygiene_settings(
        runner,
        {"compression": {"enabled": True, "threshold": 0.20, "threshold_tokens": 0}},
    )

    assert hs.threshold_pct == pytest.approx(0.85)
