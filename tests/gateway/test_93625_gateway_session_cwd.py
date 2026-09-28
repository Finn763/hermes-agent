"""Gateway-created sessions must persist a non-empty cwd (#93625).

The desktop sidebar groups sessions by workspace/cwd, so gateway rows
written with NULL/empty cwd silently vanish from view (perceived history
loss). Real SessionStore + real SessionDB, no mocks on the DB layer.
"""
from __future__ import annotations

import os
import sqlite3

from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore


def _store(tmp_path, monkeypatch) -> SessionStore:
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", tmp_path / "state.db")
    return SessionStore(sessions_dir=tmp_path, config=GatewayConfig())


def _slack_source() -> SessionSource:
    return SessionSource(
        platform=Platform.SLACK,
        user_id="U123",
        chat_id="C123",
        chat_type="channel",
        thread_id="T1",
    )


def _row_cwd(tmp_path, session_id: str):
    con = sqlite3.connect(tmp_path / "state.db")
    try:
        row = con.execute(
            "SELECT cwd FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


class TestGatewaySessionCwd:
    def test_create_persists_terminal_cwd(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", "/work/root")
        store = _store(tmp_path, monkeypatch)
        entry = store.get_or_create_session(_slack_source())
        assert (_row_cwd(tmp_path, entry.session_id) or "").strip() == "/work/root"

    def test_create_falls_back_to_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        store = _store(tmp_path, monkeypatch)
        entry = store.get_or_create_session(_slack_source())
        assert (_row_cwd(tmp_path, entry.session_id) or "").strip() == os.path.expanduser("~")
