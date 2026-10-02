"""Worker startup guard: reject claims lost in the dispatch->startup race (#22927).

A dispatcher claims ready->running THEN spawns the worker subprocess. In
that window an operator (or another tick) can block / archive / reclaim /
reassign the task. The worker must re-verify its own claim at startup and
exit 0 (not a failure) instead of running work it no longer owns.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    return home


@pytest.fixture
def conn(kanban_home):
    with kb.connect() as c:
        yield c


def _spawn_env(monkeypatch, task_id, run_id, lock):
    monkeypatch.setenv("HERMES_KANBAN_TASK", task_id)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", lock)


def test_guard_accepts_live_claim(conn, monkeypatch):
    from cli import _kanban_worker_startup_guard

    tid = kb.create_task(conn, title="live", assignee="w")
    claimed = kb.claim_task(conn, tid, claimer="lock-live")
    assert claimed is not None
    _spawn_env(monkeypatch, tid, claimed.current_run_id, "lock-live")
    assert _kanban_worker_startup_guard() is True


def test_guard_rejects_task_blocked_after_claim(conn, monkeypatch):
    from cli import _kanban_worker_startup_guard

    tid = kb.create_task(conn, title="blocked-race", assignee="w")
    claimed = kb.claim_task(conn, tid, claimer="lock-old")
    assert claimed is not None
    # Operator blocks between dispatch claim and worker startup.
    assert kb.block_task(conn, tid, reason="operator hold") is True
    _spawn_env(monkeypatch, tid, claimed.current_run_id, "lock-old")
    assert _kanban_worker_startup_guard() is False


def test_guard_rejects_stale_lock_after_reclaim(conn, monkeypatch):
    from cli import _kanban_worker_startup_guard

    tid = kb.create_task(conn, title="reclaimed-race", assignee="w")
    first = kb.claim_task(conn, tid, claimer="lock-old")
    assert first is not None
    kb.reclaim_task(conn, tid, reason="startup race")
    second = kb.claim_task(conn, tid, claimer="lock-new")
    assert second is not None
    # Stale worker presents its old run/lock: rejected.
    _spawn_env(monkeypatch, tid, first.current_run_id, "lock-old")
    assert _kanban_worker_startup_guard() is False
    # Current owner passes.
    _spawn_env(monkeypatch, tid, second.current_run_id, "lock-new")
    assert _kanban_worker_startup_guard() is True


def test_stale_worker_exit_charges_no_failure(conn):
    # A worker that exits 0 via the guard on a non-running task must not
    # trip crash accounting: status and consecutive_failures untouched.
    tid = kb.create_task(conn, title="no-charge", assignee="w")
    claimed = kb.claim_task(conn, tid, claimer="lock-x")
    assert claimed is not None
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    kb._set_worker_pid(conn, tid, dead.pid)
    assert kb.block_task(conn, tid, reason="operator hold") is True
    before = conn.execute(
        "SELECT consecutive_failures FROM tasks WHERE id=?", (tid,)
    ).fetchone()[0]
    kb._record_worker_exit(dead.pid, 0)  # rc=0, like a guard exit
    assert kb.detect_crashed_workers(conn) == []
    after = conn.execute(
        "SELECT status, consecutive_failures FROM tasks WHERE id=?", (tid,)
    ).fetchone()
    assert after["status"] == "blocked"
    assert after["consecutive_failures"] == before
