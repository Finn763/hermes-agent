"""Multiplexed per-profile cron tick: kanban author honors the ContextVar identity.

Regression (#119859): ``kanban_comment`` author / ``kanban_create``
``created_by`` / the own-comment skip read raw
``os.environ["HERMES_PROFILE"]``. Under a multiplexed tick the active profile
travels via the ``set_hermes_home_override`` ContextVar (never mirrored into
``os.environ``), so every board write fell back to the literal ``"worker"``
and a worker's own notes could re-enter the live turn as operator steering.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_WORKTREE = Path(__file__).resolve().parents[2]
if str(_WORKTREE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE))

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
import tools.kanban_tools as kt


class FakeAgent:
    def __init__(self):
        self.steers: list[str] = []

    def steer(self, text: str) -> bool:
        self.steers.append(text)
        return True


@pytest.fixture
def multiplex_tick(tmp_path, monkeypatch):
    """Fleet root in env; ticking profile carried ONLY by the ContextVar override."""
    import hermes_constants

    root = tmp_path / "fleet"
    (root / "profiles" / "orchestrator").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kb.db"))
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_PROFILE_NAME", raising=False)
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    for var in ("HERMES_KANBAN_HOME", "HERMES_KANBAN_BOARD",
                "HERMES_KANBAN_WORKSPACES_ROOT"):
        monkeypatch.delenv(var, raising=False)
    hermes_constants._default_hermes_root_memo = None  # type: ignore[attr-defined]
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    kt._comment_watermark.clear()
    kt._comment_poll_last_attempt = 0.0
    token = hermes_constants.set_hermes_home_override(
        str(root / "profiles" / "orchestrator"))
    try:
        yield root
    finally:
        hermes_constants.reset_hermes_home_override(token)
        hermes_constants._default_hermes_root_memo = None  # type: ignore[attr-defined]


def _task(multiplex_tick):
    conn = kbc.connect()
    try:
        return kb.create_task(conn, title="tick task")
    finally:
        conn.close()


def _last_comment_author(tid: str) -> str:
    conn = kbc.connect()
    try:
        return kb.list_comments(conn, tid)[-1].author
    finally:
        conn.close()


def test_comment_author_uses_tick_profile(multiplex_tick, monkeypatch):
    tid = _task(multiplex_tick)
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    out = json.loads(kt._handle_comment({"task_id": tid, "body": "nudge"}))
    assert out["ok"], out
    assert _last_comment_author(tid) == "orchestrator"


def test_create_created_by_uses_tick_profile(multiplex_tick, monkeypatch):
    tid = _task(multiplex_tick)
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    out = json.loads(kt._handle_create(
        {"title": "tick child", "assignee": "peer", "parents": [tid]}))
    assert out["ok"], out
    conn = kbc.connect()
    try:
        assert kb.get_task(conn, out["task_id"]).created_by == "orchestrator"
    finally:
        conn.close()


def _unthrottle():
    kt._comment_poll_last_attempt = 0.0


def test_own_tick_comment_not_steered(multiplex_tick, monkeypatch):
    tid = _task(multiplex_tick)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    agent = FakeAgent()
    _unthrottle()
    kt.inject_new_comments_from_env(agent)  # seed
    conn = kbc.connect()
    try:
        kb.add_comment(conn, tid, author="orchestrator", body="my own note")
    finally:
        conn.close()
    _unthrottle()
    assert kt.inject_new_comments_from_env(agent) is False
    assert agent.steers == []
