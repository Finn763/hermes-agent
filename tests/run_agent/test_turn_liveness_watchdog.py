"""Turn liveness watchdog (#95548): force-abort turns that stall silently.

Regression coverage for the gateway turn hang reported in #95548: a turn can
stall in the middle (observed between "model returned tool_calls" and tool
execution) with no error logged, no further progress, and the durable turn
lease kept renewing — so nothing ever force-aborts it and the session is
stuck until the gateway process is killed.

The fix adds a turn liveness watchdog next to the durable lease refresher in
``AIAgent.run_conversation``. It keys off the agent's activity clock
(``_last_activity_ts`` — the #72039 single progress source, which lease
renewal never touches). When a turn shows no observable progress for the
configured bound (``HERMES_TURN_LIVENESS_WATCHDOG_S``), it:

1. logs the stall loudly (surface instead of silent blocking),
2. force-interrupts the turn so it unwinds as an interrupted turn,
3. stops lease renewal so the durable lease lapses and stale-turn cleanup
   can reclaim the session even if the hard interrupt cannot unwind a
   truly wedged loop.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from run_agent import AIAgent


class _DB:
    def __init__(self, session_exists=True, acquire_result=True):
        self.events = []
        self.refresh_times = []
        self.session_exists = session_exists
        self.acquire_result = acquire_result

    def get_session(self, session_id):
        return {"id": session_id} if self.session_exists else None

    def acquire_session_turn_lease(self, session_id, holder, **kwargs):
        self.events.append(("acquire", session_id, holder))
        on_wait = kwargs.get("on_wait")
        if on_wait is not None and self.acquire_result is False:
            on_wait(0.0)
        return self.acquire_result

    def resolve_resume_session_id(self, session_id):
        self.events.append(("resolve", session_id))
        return session_id

    def get_messages_as_conversation(self, session_id, **kwargs):
        self.events.append(("reload", session_id, kwargs))
        return [{"role": "user", "content": "durable latest"}]

    def refresh_session_turn_lease(self, session_id, holder, **kwargs):
        self.events.append(("refresh", session_id, holder))
        self.refresh_times.append(time.time())
        return True

    def release_session_turn_lease(self, session_id, holder):
        self.events.append(("release", session_id, holder))


def _agent_with_db(db, *, session_id="stalled-session", platform="desktop"):
    agent = AIAgent.__new__(AIAgent)
    agent.session_id = session_id
    agent.platform = platform
    agent.model = "test-model"
    agent._session_db = db
    agent._session_db_created = True
    agent._persist_disabled = False
    agent._parent_session_id = None
    agent._relay_pending_turn_id = None
    agent._reset_activity_labels_after_turn = lambda: None
    agent._conversation_root_id = lambda: session_id
    agent.log_prefix = ""
    agent._vprint = lambda *a, **k: None
    agent.status_callback = None
    agent._interrupt_requested = False
    agent._interrupt_message = None
    agent._pending_redirect = None
    agent._execution_thread_id = None
    agent._interrupt_thread_signal_pending = False
    agent._hard_interrupt_requested = threading.Event()
    agent._active_children_lock = threading.Lock()
    agent._active_children = set()
    agent.quiet_mode = True
    # A real cached agent entering a new turn holds the activity clock
    # from its PREVIOUS turn: `_reset_activity_labels_after_turn` keeps
    # `_last_activity_ts` across turns by design, so an agent that sat
    # idle longer than the watchdog bound (user walked away, came back,
    # sent a message) enters with a STALE clock. `AIAgent.run_conversation`
    # stamps the clock at turn entry (#95663 review), so the watchdog
    # measures idle from THIS turn's start — mirror that reality: stale
    # entry clock, fresh measurement after the wrapper's turn-entry stamp.
    agent._last_activity_ts = time.time() - 1000.0
    agent._last_activity_desc = "previous turn (idle)"
    agent._session_turn_lease_refresh_interval = 60.0
    return agent


@pytest.fixture
def watchdog_env(monkeypatch):
    monkeypatch.setenv("HERMES_TURN_LIVENESS_WATCHDOG_S", "0.3")
    monkeypatch.setenv("HERMES_TURN_LIVENESS_WATCHDOG_POLL_S", "0.05")
    return monkeypatch


def _run_turn(agent, inner_loop, monkeypatch):
    """Drive AIAgent.run_conversation with a fake inner conversation loop."""
    from agent import conversation_loop as loop_module

    monkeypatch.setattr(loop_module, "run_conversation", inner_loop)
    return AIAgent.run_conversation(
        agent,
        "new message",
        conversation_history=[{"role": "user", "content": "stale"}],
    )


def test_watchdog_force_aborts_silently_stalled_turn(watchdog_env, monkeypatch, caplog):
    """A turn with zero observable progress past the bound is surfaced and
    force-aborted as an interrupted turn instead of hanging forever."""
    db = _DB()
    agent = _agent_with_db(db)

    interrupt_seen = {}
    t_start = time.time()

    def stalled_loop(_agent, _message, _system, history, *_args, **_kwargs):
        # Simulate the #95548 zombie: the loop makes no progress and never
        # touches the activity clock. It only notices the watchdog's
        # hard interrupt (real wedges may not even do that — see the lease
        # test below).
        while not _agent._interrupt_requested:
            if time.time() - t_start > 10:
                break
            time.sleep(0.005)
        interrupt_seen["at"] = time.time()
        interrupt_seen["message"] = _agent._interrupt_message
        return {
            "final_response": "aborted",
            "messages": history,
            "api_calls": 0,
            "completed": False,
            "interrupted": True,
        }

    with caplog.at_level(logging.ERROR, logger="run_agent"):
        result = _run_turn(agent, stalled_loop, monkeypatch)

    elapsed = time.time() - t_start

    # The turn was surfaced as interrupted, not hung.
    assert result["interrupted"] is True
    assert result["final_response"] == "aborted"
    # The watchdog fired before our 10s outer bound, and after the 0.3s idle
    # bound (poll interval makes the exact fire instant approximate).
    assert 0.2 <= elapsed < 10.0
    # The stall was logged loudly with the session named.
    assert any(
        "Turn liveness watchdog fired" in record.getMessage()
        and "stalled-session" in record.getMessage()
        for record in caplog.records
    )
    # The interrupt message tells the UI why the turn ended.
    assert agent._interrupt_message is None  # cleared by the wrapper's finally
    assert "no progress" in (interrupt_seen.get("message") or "")  # watchdog fired
    # The durable lease was released on the interrupted exit path.
    assert db.events[-1][0] == "release"
    assert db.events[-1][1] == "stalled-session"


def test_watchdog_does_not_fire_while_turn_still_making_progress(
    watchdog_env, monkeypatch, caplog
):
    """A turn that keeps touching the activity clock (API waits, stream
    tokens, tool heartbeats, tool completions) is never force-aborted, and
    the lease keeps renewing normally."""
    db = _DB()
    agent = _agent_with_db(db)
    # Fast lease refresh so the test can prove renewal stayed alive.
    agent._session_turn_lease_refresh_interval = 0.05
    t_start = time.time()

    def busy_loop(_agent, _message, _system, history, *_args, **_kwargs):
        # Keep making progress well past the 0.3s idle bound.
        while time.time() - t_start < 0.6:
            _agent._touch_activity("test tick")
            time.sleep(0.02)
        return {
            "final_response": "done",
            "messages": history,
            "api_calls": 1,
            "completed": True,
        }

    with caplog.at_level(logging.ERROR, logger="run_agent"):
        result = _run_turn(agent, busy_loop, monkeypatch)

    assert result["completed"] is True
    assert result.get("interrupted") is not True
    assert agent._interrupt_requested is False
    assert not any(
        "Turn liveness watchdog fired" in record.getMessage()
        for record in caplog.records
    )
    # The lease refresher ran during the turn — renewal is orthogonal to the
    # watchdog and continued while the turn was alive.
    assert len(db.refresh_times) >= 1


def test_watchdog_stops_lease_renewal_when_interrupt_cannot_unwind_wedge(
    watchdog_env, monkeypatch
):
    """The issue's 'lease keeps renewing' masking: even when the hard
    interrupt cannot immediately unwind the loop (a truly wedged frame), the
    watchdog stops renewing the durable lease so TTL expiry lets stale-turn
    cleanup reclaim the session."""
    db = _DB()
    agent = _agent_with_db(db)
    agent._session_turn_lease_refresh_interval = 0.05
    t_start = time.time()
    fire_ts = {}

    def wedged_loop(_agent, _message, _system, history, *_args, **_kwargs):
        # Notice the interrupt but keep "wedging" (no activity) for another
        # half second — simulating a blocked frame the interrupt cannot free
        # immediately.
        while not _agent._interrupt_requested:
            if time.time() - t_start > 10:
                break
            time.sleep(0.005)
        fire_ts["at"] = time.time()
        while time.time() - fire_ts["at"] < 0.5:
            time.sleep(0.005)
        return {
            "final_response": "recovered",
            "messages": history,
            "api_calls": 0,
            "completed": True,
        }

    result = _run_turn(agent, wedged_loop, monkeypatch)
    assert result["completed"] is True
    assert time.time() - t_start < 10.0

    # The watchdog fired during the wedge.
    assert "at" in fire_ts
    # Once the watchdog fired, lease renewal stopped: refreshes cadence at
    # 0.05s would have produced ~8 more events in the 0.5s post-fire wedge if
    # renewal had continued. Tolerate only in-flight refreshes racing the
    # stop (<= 0.15s window).
    late_refreshes = [
        t for t in db.refresh_times if t > fire_ts["at"] + 0.15
    ]
    assert late_refreshes == [], (
        f"lease renewal continued after watchdog fired: {len(late_refreshes)} "
        "post-fire refreshes"
    )
    # The lease row was still released when the turn finally unwound.
    assert [e[0] for e in db.events][-1] == "release"
