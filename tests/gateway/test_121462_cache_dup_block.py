"""RED repro probe for #121462: gateway cache invalidation can append an
identical active transcript block after compaction.

Models the incident boundary with a real SessionDB + real AIAgent flush +
real gateway replay builder:

1. seed a transcript (user/assistant/tool mix),
2. run a real in-place ``compress_context`` commit (fake summarizer),
3. simulate cache invalidation: a FRESH agent rehydrates via
   ``get_messages_as_conversation`` (born-durable stamps) +
   ``_build_gateway_agent_history``,
4. run the turn-start identity flush and the codex-shaped marker-only
   flush (``_persist_projected_messages`` flushes with NO
   conversation_history),
5. assert the active set never grows by a duplicate block.

Plus a diagnostic probe showing where the replay builder drops the
born-durable marker, and a lost-cursor probe (marker-only flush with no
preceding identity flush on the same dicts).
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_agent(session_db, session_id):
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            session_db=session_db,
            session_id=session_id,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.compression_in_place = True

    def _fake_compress(messages, current_tokens=None, focus_topic=None, force=False):
        # Mirrors the real compress() contract: marker-swept fresh dicts.
        return [
            {"role": "user", "content": "[CONTEXT COMPACTION] summary of prior turns"},
            {"role": "assistant", "content": "recent reply 1"},
            {"role": "user", "content": "follow-up q"},
            {"role": "assistant", "content": "recent reply 2"},
        ]

    agent.context_compressor.compress = _fake_compress
    agent.context_compressor._last_compress_aborted = False
    agent.context_compressor._last_summary_error = None
    agent.context_compressor.compression_count = 1
    return agent


def _seed(db, sid, n_turns=4):
    db.create_session(sid, "telegram", model="test/model")
    for i in range(n_turns):
        db.append_message(session_id=sid, role="user", content=f"user turn {i}")
        db.append_message(
            session_id=sid, role="assistant", content=f"assistant reply {i}",
            tool_calls=[{"id": f"call-{i}", "function": {"name": "exec_command", "arguments": "{}"}}],
        )
        db.append_message(
            session_id=sid, role="tool", content=f"tool result {i}", tool_call_id=f"call-{i}",
        )


def _counts(db, sid):
    total = db._conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    active = db._conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ? AND active = 1", (sid,)
    ).fetchone()[0]
    return total, active


def _dup_active_contents(db, sid):
    return db._conn.execute(
        "SELECT content, COUNT(*) c FROM messages "
        "WHERE session_id = ? AND active = 1 "
        "GROUP BY content HAVING c > 1",
        (sid,),
    ).fetchall()


def _rehydrate(agent_history_source):
    """Gateway rehydration shape: born-durable load -> replay builder."""
    from gateway.run import _build_gateway_agent_history

    agent_history, _ = _build_gateway_agent_history(agent_history_source)
    return agent_history


class TestCacheInvalidationDupBlock:
    def test_replay_preserves_born_durable_markers(self):
        """Contract: every replayed row keeps _db_persisted (#121462).
        Plain user/assistant entries lost it before the fix, so a
        marker-only flush re-appended the block after rehydration."""
        from hermes_state import SessionDB
        from agent.context_compressor import _DB_PERSISTED_MARKER

        with tempfile.TemporaryDirectory() as tmp:
            db = SessionDB(db_path=Path(tmp) / "t.db")
            try:
                sid = "20260924_diag"
                _seed(db, sid)
                loaded = db.get_messages_as_conversation(sid)
                assert loaded and all(m.get(_DB_PERSISTED_MARKER) for m in loaded)
                replayed = _rehydrate(loaded)
                missing = [m.get("role") for m in replayed if not m.get(_DB_PERSISTED_MARKER)]
                assert missing == [], f"replayed rows lost the marker: {missing}"
            finally:
                db.close()

    def test_lost_cursor_marker_only_flush_reappends_block(self):
        """Lost-cursor probe: a marker-only flush (codex shape, no
        conversation_history) over replayed dicts that never saw an
        identity flush must not re-append the block. RED if it does."""
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmp:
            db = SessionDB(db_path=Path(tmp) / "t.db")
            try:
                sid = "20260924_cursor"
                _seed(db, sid)
                _, active_before = _counts(db, sid)
                agent = _make_agent(db, sid)
                agent._session_db_created = True

                # Fresh-agent rehydration AFTER invalidation: born-durable load,
                # then the gateway replay builder (strips user/assistant markers).
                loaded = db.get_messages_as_conversation(sid)
                agent_history = _rehydrate(loaded)
                messages = list(agent_history) + [{"role": "user", "content": "new question"}]
                # NOTE: no identity flush first -- the lost-cursor condition.
                agent._flush_messages_to_session_db(messages)  # marker-only, codex shape
                _, active_after = _counts(db, sid)
                dups = _dup_active_contents(db, sid)
                print(f"\nactive {active_before} -> {active_after}, dup groups: {len(dups)}")
                assert active_after == active_before + 1, (
                    f"marker-only flush re-appended the block: active {active_before} -> {active_after}"
                )
                assert dups == []
            finally:
                db.close()

    def test_invalidation_after_compaction_no_dup(self):
        """Full incident shape: compact -> invalidate (fresh agent) ->
        rehydrate -> turn-start identity flush -> marker-only flush.
        Must stay green; documents the guarded chain."""
        from hermes_state import SessionDB
        from agent.conversation_compression import compress_context

        with tempfile.TemporaryDirectory() as tmp:
            db = SessionDB(db_path=Path(tmp) / "t.db")
            try:
                sid = "20260924_full"
                _seed(db, sid, n_turns=8)  # 24 rows
                agent_a = _make_agent(db, sid)
                agent_a._session_db_created = True
                agent_a._last_flushed_db_idx = 24

                messages = [{"role": "user", "content": f"m{i}"} for i in range(24)]
                compressed, _sp = compress_context(
                    agent_a, messages, approx_tokens=100_000, system_message="sys"
                )
                total_c, active_c = _counts(db, sid)
                assert active_c == len(compressed) == 4

                # Invalidation: fresh agent, rehydrate from the compacted DB.
                agent_b = _make_agent(db, sid)
                agent_b._session_db_created = True
                loaded = db.get_messages_as_conversation(sid)
                agent_history = _rehydrate(loaded)
                turn_messages = list(agent_history) + [{"role": "user", "content": "next q"}]
                agent_b._flush_messages_to_session_db(turn_messages, agent_history)
                agent_b._flush_messages_to_session_db(turn_messages)  # codex shape
                total2, active2 = _counts(db, sid)
                dups = _dup_active_contents(db, sid)
                print(f"\ncompacted active {active_c} -> {active2}, total {total_c} -> {total2}")
                assert active2 == active_c + 1, (total_c, active_c, total2, active2, dups)
                assert dups == []
            finally:
                db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
