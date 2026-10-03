"""Issue #63504: dispatcher must fail closed with an actionable reason when a
worker dies at startup on provider/model misconfiguration.

Evidence in the issue: run 106 exited rc=0 with no terminal kanban call
(protocol violation), runs 107/108 died as ``pid N not alive`` (no reap
entry — typical of a startup crash), while cron errors showed
``OpenAI Codex rejected grok-4.5`` + ``fallback to xai-oauth failed
because provider not configured``. The dispatcher recorded only bare
``pid N not alive`` / generic protocol-violation text, so the board
looped instead of blocking with the config reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_cli.kanban_db as _kb
from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _exited_status(code: int) -> int:
    return code << 8


def _claim_with_dead_pid(conn, tid, pid, host, tag):
    kb.claim_task(conn, tid, claimer=f"{host}:{tag}")
    conn.execute(
        "UPDATE tasks SET worker_pid=? WHERE id=?",
        (pid, tid),
    )
    conn.commit()


def _write_worker_log(tid: str, text: str) -> None:
    log_dir = kb.worker_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{tid}.log").write_text(text, encoding="utf-8")


def test_unknown_exit_with_config_error_blocks_actionable(
    kanban_home, monkeypatch,
):
    """'pid not alive' + provider/config error in the worker log must stamp
    an actionable blocker (fail closed), not a bare pid message."""
    monkeypatch.setattr(_kb, "_pid_alive", lambda _pid: False)
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")

    with kb.connect() as conn:
        host = _kb._claimer_id().split(":", 1)[0]
        tid = kb.create_task(conn, title="cfg", assignee="dan-pr")
        pid = 81146
        _claim_with_dead_pid(conn, tid, pid, host, "w0")
        # No reap-registry entry -> kind "unknown" -> "pid N not alive".
        _write_worker_log(
            tid,
            "booting worker...\n"
            "OpenAI Codex rejected `grok-4.5`; "
            "fallback to `xai-oauth` failed because provider not configured\n",
        )

        crashed = kb.detect_crashed_workers(conn)

        assert tid in crashed
        task = kb.get_task(conn, tid)
        assert task.last_failure_error, "must stamp a failure reason"
        assert "pid 81146 not alive" not in (task.last_failure_error or "")
        assert "provider" in (task.last_failure_error or "").lower()
        # Guard must defer respawn as a deterministic config/auth blocker.
        assert kb.check_respawn_guard(conn, tid) == "blocker_auth"


def test_clean_exit_with_config_error_is_not_protocol_violation(
    kanban_home, monkeypatch,
):
    """rc=0 with a provider/config error in the log is a startup failure,
    not a protocol violation — it must not consume the violation budget
    with generic guidance text."""
    monkeypatch.setattr(_kb, "_pid_alive", lambda _pid: False)
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")

    with kb.connect() as conn:
        host = _kb._claimer_id().split(":", 1)[0]
        tid = kb.create_task(conn, title="cfg-clean", assignee="dan-pr")
        pid = 82201
        _claim_with_dead_pid(conn, tid, pid, host, "w0")
        _kb._record_worker_exit(pid, _exited_status(0))
        _write_worker_log(
            tid,
            "worker output...\n"
            "fallback to `xai-oauth` failed because provider not configured\n",
        )

        kb.detect_crashed_workers(conn)

        task = kb.get_task(conn, tid)
        assert task.last_failure_error, "must stamp a failure reason"
        assert "protocol violation" not in (task.last_failure_error or "").lower()
        assert "provider" in (task.last_failure_error or "").lower()
        rows = conn.execute(
            "SELECT metadata FROM task_runs WHERE task_id=?", (tid,)
        ).fetchall()
        import json

        assert rows, "a run must be recorded"
        assert not any(
            r["metadata"] and json.loads(r["metadata"]).get("protocol_violation")
            for r in rows
        ), "must not be counted as a protocol violation"
