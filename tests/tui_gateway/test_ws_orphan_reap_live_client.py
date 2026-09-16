"""The WS-orphan reclaim judgement must see a client that is still attached.

``_ws_session_is_detached`` is the single gate behind both reclaim producers — the grace reap Timer and the
reaper's periodic re-arm scan (``_REAPER_SCAN_S`` = 300s) — so a parked record whose client never made it
back into the transport slot must not be read as an orphan, and the scan must not re-arm a reclaim against
it every 5 minutes. The protection face is exercised too: a session with no live client (nothing
registered, only a closed viewer, or only the process-wide stdio sink) is still reclaimed, so the shared
predicate cannot be satisfied by a dead registration.
"""

import threading

import pytest

from tui_gateway import server


class _LiveTransport:
    """A peer that is live by the same evidence the gateway itself uses (no ``_closed`` latch)."""

    def write(self, *a, **k):
        return True


def _live_transport():
    return _LiveTransport()


def _dead_transport():
    dead = _LiveTransport()
    dead._closed = True  # the only trace a socket that vanished without a disconnect leaves behind
    return dead


def _parked_session(**extra):
    session = {
        "agent": None, "agent_ready": threading.Event(), "session_key": "stored-sid",
        "history": [], "history_version": 0, "history_lock": threading.Lock(),
        "running": False, "attached_images": [], "cols": 80, "source": "desktop",
        "inflight_turn": None, "transport": server._detached_ws_transport,
    }
    session.update(extra)
    return session


def _reap_harness(monkeypatch):
    """Capture grace Timers instead of running them, plus the sessions the reaper claims. Returns
    ``(timers, reaped)``; fire a capture with ``timer.callback()``."""
    timers, reaped = [], []

    class _Timer:
        def __init__(self, delay, callback):
            self.delay, self.callback, self.daemon, self.cancelled = delay, callback, False, False
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(server.threading, "Timer", _Timer)
    monkeypatch.setattr(server, "_session_has_active_delegations", lambda *a, **k: False)
    monkeypatch.setattr(
        server, "_teardown_popped_session",
        lambda claimed, *, end_reason: reaped.append((claimed, end_reason)) or True)
    return timers, reaped


def test_parked_session_with_live_client_is_not_reaped(monkeypatch):
    """Regression: a parked record with a live client registered is NOT an orphan.

    The registration is the one ``_rebind_live_transport`` writes (resume/activate through
    ``_live_session_payload``), i.e. a client the disconnect path would re-bind to — the gate must not
    reclaim the session out from under it while the slot still reads the drop sentinel.
    """
    sid, live = "live-client-sid", _live_transport()
    session = _parked_session(viewers={live: 1000.0})
    monkeypatch.setattr(server, "_sessions", {sid: session})
    monkeypatch.setattr(server, "_pending_ws_reaps", {})
    _timers, reaped = _reap_harness(monkeypatch)

    server._schedule_ws_orphan_reap(sid)
    server._pending_ws_reaps[sid].callback()

    assert server._sessions.get(sid) is session, (
        "a parked session with a live client attached must not be judged orphan and reaped")
    assert reaped == []
    assert server._ws_session_is_detached(session) is False, (
        "a parked session a client is still attached to must not read as detached/orphaned")
    assert sid not in server._pending_ws_reaps  # the claim stands down; the client's own disconnect re-arms


def test_periodic_rearm_scan_does_not_arm_reap_for_parked_session_with_live_client(monkeypatch):
    """Regression: the reaper's scan must not re-arm a reclaim against a session a client still holds.

    ``_repair_missing_ws_orphan_reaps`` runs on every ``_REAPER_SCAN_S`` (300s) tick, so arming one here is
    a periodic reclaim of an in-use session — the loop the report sees every few minutes.
    """
    sid, live = "live-client-scan-sid", _live_transport()
    session = _parked_session(viewers={live: 1000.0})
    monkeypatch.setattr(server, "_sessions", {sid: session})
    monkeypatch.setattr(server, "_pending_ws_reaps", {})
    timers, _reaped = _reap_harness(monkeypatch)

    server._repair_missing_ws_orphan_reaps()

    assert timers == [], "no grace Timer may be armed for a parked session with a live client"
    assert sid not in server._pending_ws_reaps


@pytest.mark.parametrize("registration", ["nothing", "dead_viewer", "stdio_viewer"])
def test_parked_session_without_a_live_client_is_still_reaped(monkeypatch, registration):
    """Protection face: the shared predicate must not keep a real orphan alive.

    ``dead_viewer`` is a registration whose socket vanished (the ``_closed`` latch is the only trace);
    ``stdio_viewer`` is the process-wide stdio sink a standalone TUI resume stamps into ``viewers`` — it is
    never a live peer, so it must not confer immortality on the record it is registered against.
    """
    sid = f"orphan-{registration}-sid"
    viewers = {
        "nothing": {},
        "dead_viewer": {_dead_transport(): 1000.0},
        "stdio_viewer": {server._stdio_transport: 1000.0},
    }[registration]
    session = _parked_session(viewers=viewers)
    monkeypatch.setattr(server, "_sessions", {sid: session})
    monkeypatch.setattr(server, "_pending_ws_reaps", {})
    _timers, reaped = _reap_harness(monkeypatch)

    server._schedule_ws_orphan_reap(sid)
    timer = server._pending_ws_reaps[sid]
    timer.callback()

    assert sid not in server._sessions
    assert reaped == [(session, "ws_orphan_reap")]
    assert sid not in server._pending_ws_reaps


def test_rearm_scan_still_arms_reap_for_clientless_parked_session(monkeypatch):
    """Protection face for the periodic scan: a truly clientless parked record keeps its reclaim."""
    sid = "orphan-scan-sid"
    session = _parked_session()
    monkeypatch.setattr(server, "_sessions", {sid: session})
    monkeypatch.setattr(server, "_pending_ws_reaps", {})
    timers, _reaped = _reap_harness(monkeypatch)

    server._repair_missing_ws_orphan_reaps()

    assert sid in server._pending_ws_reaps, "the scan must still repair a lost reap for a real orphan"
    assert timers and timers[-1].delay == server._WS_ORPHAN_REAP_GRACE_S
