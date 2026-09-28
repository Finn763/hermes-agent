"""Teardown must not silently discard pending gateway approvals (#106678).

The WS-orphan reap tears a session down while an approval prompt is still
parked in ``tools.approval._gateway_queues``. The unregister pops the entry,
the agent-side wait resolves as deny/timeout, and a reconnecting client gets
a bare 4001 on ``approval.pending`` — the prompt simply never arrives.

Loud-deny contract: every approval dropped by teardown emits one
``approval.cancelled`` broadcast (same channel as ``session.reclaimed``) so
the surface can show it. Nothing pending -> no event, so repeat teardowns
stay silent.
"""

import pytest

from tools.approval import _ApprovalEntry, _gateway_queues
from tui_gateway import server


@pytest.fixture()
def captured(monkeypatch):
    events = []
    monkeypatch.setattr(
        server,
        "_broadcast_global_event",
        lambda ev, payload=None: events.append((ev, payload)),
    )
    # Teardown's real work (finalize, agent close) is out of scope — this is
    # about what reaches the client. The real unregister_gateway_notify runs.
    monkeypatch.setattr(server, "_finalize_session", lambda *a, **k: None)
    return events


def _session(key="20260909_120000_aaaaaa"):
    return {"_sid": "live-abc", "session_key": key}


@pytest.fixture()
def pending_key():
    key = "20260909_120000_aaaaaa"
    entry = _ApprovalEntry(
        {
            "command": "rm -rf /tmp/x",
            "description": "delete dir",
            "pattern_key": "rm-rf",
            "pattern_keys": ["rm-rf"],
        }
    )
    _gateway_queues.setdefault(key, []).append(entry)
    yield key, entry.data["request_id"]
    _gateway_queues.pop(key, None)


@pytest.mark.parametrize("reason", ["ws_orphan_reap", "idle_timeout", "tui_close"])
def test_teardown_announces_dropped_approval(captured, pending_key, reason):
    """A teardown holding a pending approval is loud, whatever the reason."""
    key, request_id = pending_key

    server._teardown_session(_session(key), end_reason=reason)

    assert _gateway_queues.get(key) is None  # drained, not leaked
    cancelled = [p for ev, p in captured if ev == "approval.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["request_id"] == request_id
    assert cancelled[0]["command"] == "rm -rf /tmp/x"
    assert cancelled[0]["stored_session_id"] == key
    assert cancelled[0]["session_id"] == "live-abc"
    assert cancelled[0]["reason"] == reason


def test_teardown_without_pending_stays_silent(captured):
    server._teardown_session(_session("20260909_120000_empty"), end_reason="ws_orphan_reap")

    assert [ev for ev, _ in captured if ev == "approval.cancelled"] == []


def test_repeat_teardown_stays_silent(captured, pending_key):
    """Idempotent teardown: the second pass drops nothing, announces nothing."""
    key, _ = pending_key

    server._teardown_session(_session(key), end_reason="ws_orphan_reap")
    server._teardown_session(_session(key), end_reason="ws_orphan_reap")

    assert len([ev for ev, _ in captured if ev == "approval.cancelled"]) == 1


def test_broadcast_failure_does_not_break_teardown(monkeypatch, pending_key):
    finalized = []

    def _boom(*_a, **_k):
        raise RuntimeError("transport gone")

    monkeypatch.setattr(server, "_broadcast_global_event", _boom)
    monkeypatch.setattr(
        server, "_finalize_session", lambda s, **k: finalized.append(k.get("end_reason"))
    )

    key, _ = pending_key
    server._teardown_session(_session(key), end_reason="ws_orphan_reap")

    assert finalized == ["ws_orphan_reap"]
    assert _gateway_queues.get(key) is None
