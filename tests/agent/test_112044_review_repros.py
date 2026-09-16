"""Independent verification of the two P1 findings on PR #112044 (reviewer ehz0ah, head b715156bdc).

Written from the reviewer's exact scenarios, NOT from the PR's own tests:

A. `/branch` copies lose platform identity (`_persist_branch` copies role/content/timestamp) —
   two valid distinct Telegram rows must both survive the branch copy.
B. The alternation-repair path must fuse two adjacent assistant rows into ONE durable row
   (nonzero repair count), not leave the predecessors active (3 -> 4).
C. The principle behind both: a missing platform id cannot establish identity. A brand-new
   message that shares role/content/timestamp with an existing row — and carries no durable
   provenance — is a distinct event and must insert, not be reconciled away.
"""

import tempfile
from pathlib import Path

SESSION_ID = "review-repro-112044"


def _db(tmpdir, session_id=SESSION_ID):
    from hermes_state import SessionDB

    db = SessionDB(db_path=Path(tmpdir) / "t.db")
    db.create_session(session_id=session_id, source="test")
    return db


def _agent(db, session_id=SESSION_ID):
    import os
    from unittest.mock import patch

    from run_agent import AIAgent

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        agent = AIAgent(
            api_key="test-key", base_url="https://openrouter.ai/api/v1", model="test/model",
            quiet_mode=True, session_db=db, session_id=session_id,
            skip_context_files=True, skip_memory=True,
        )
    agent._ensure_db_session()
    return agent


def test_a_branch_copy_keeps_both_distinct_platform_events(tmp_path):
    """Reviewer repro A: parent holds two same-text/same-second Telegram rows with different ids;
    `/branch` must copy BOTH, not collapse them to one."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _db(tmpdir)
        try:
            db.append_messages_batch(SESSION_ID, [
                {"role": "user", "content": "ok", "timestamp": 1_700_000_000.0,
                 "platform_message_id": "telegram-100"},
                {"role": "user", "content": "ok", "timestamp": 1_700_000_000.0,
                 "platform_message_id": "telegram-101"},
            ])
            assert len(db.get_messages(SESSION_ID)) == 2, "parent seed itself lost a row"

            from tui_gateway import methods_session as ms
            from tui_gateway.methods_session import _BRANCH_COPY_FIELDS, _persist_branch

            # The gateway binds this at bootstrap (tui_gateway/server.py:_resolve_model).
            ms._resolve_model = lambda: "test/model"
            child = "review-repro-112044-child"
            _persist_branch(
                db, child, SESSION_ID, "branch of review repro",
                db.get_messages(SESSION_ID),
                source="test", cwd=None, profile_name=None, copy_fields=_BRANCH_COPY_FIELDS,
            )

            rows = db.get_messages(child)
            assert len(rows) == 2, (
                f"/branch wrote {len(rows)} row(s) for a 2-event parent transcript "
                f"(a missing platform id cannot establish identity; both events must survive)"
            )
        finally:
            db.close()


def test_b_repair_merge_fuses_predecessors_into_one_row(tmp_path):
    """Reviewer repro B: two adjacent active assistant rows (text + tool_calls), markers removed,
    repaired and flushed -> repair count 1 and the durable transcript holds the fused row."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _db(tmpdir)
        try:
            agent = _agent(db)
            db.append_messages_batch(SESSION_ID, [
                {"role": "user", "content": "run it", "timestamp": 1_700_000_000.0},
                {"role": "assistant", "content": "thinking aloud", "timestamp": 1_700_000_001.0},
                {"role": "assistant", "content": "", "timestamp": 1_700_000_002.0,
                 "finish_reason": "tool_calls",
                 "tool_calls": [{"id": "call_fuse_1", "type": "function",
                                 "function": {"name": "terminal", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "call_fuse_1", "content": "output",
                 "timestamp": 1_700_000_003.0},
            ])
            assert len(db.get_messages(SESSION_ID)) == 4

            from agent.agent_runtime_helpers import repair_message_sequence_with_cursor

            live = db.get_messages_as_conversation(SESSION_ID, include_row_ids=True)
            repairs = repair_message_sequence_with_cursor(agent, live)
            assert repairs > 0, "the repair pass did not exercise _merge_assistant_into"
            agent._flush_messages_to_session_db(live, None)

            rows = db.get_messages(SESSION_ID)
            assert len(rows) == 3, (
                f"repair left its predecessors active: {len(rows)} active rows (expected 3 — "
                f"the fused assistant row replaces both originals)"
            )
            fused = [r for r in rows if r["role"] == "assistant"]
            assert len(fused) == 1
            assert "thinking aloud" in (fused[0]["content"] or "")
        finally:
            db.close()


def test_c_new_message_without_provenance_is_not_swallowed(tmp_path):
    """Reviewer principle: no platform id => no identity proof. A second, genuinely new message
    with the same role/content/timestamp as an existing row carries no durable provenance and
    must insert (data loss otherwise)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _db(tmpdir)
        try:
            first = db.append_messages_batch(SESSION_ID, [
                {"role": "user", "content": "ok", "timestamp": 1_700_000_000.0}])
            second = db.append_messages_batch(SESSION_ID, [
                {"role": "user", "content": "ok", "timestamp": 1_700_000_000.0}])
            assert first == 1, "seed row did not land"
            assert second == 1, (
                "a new message with no platform id and no durable provenance was reconciled away "
                "(content identity alone cannot prove it IS the same durable event)"
            )
            assert len(db.get_messages(SESSION_ID)) == 2
        finally:
            db.close()


def test_e_marker_swept_copy_of_a_load_without_row_ids_still_reconciles(tmp_path):
    """The #111996 shape exactly: a transcript loaded WITHOUT row ids is born-durable, and compaction
    assembly sweeps that marker (``_fresh_compaction_message_copy``). The copy must keep a provenance
    claim of its own, or the flush re-persists an entire duplicate generation (3 -> 6)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _db(tmpdir)
        try:
            db.append_messages_batch(SESSION_ID, _transcript())
            before = len(db.get_messages(SESSION_ID))
            assert before == 3

            from agent.context_compressor import _fresh_compaction_message_copy

            loaded = db.get_messages_as_conversation(SESSION_ID)  # no include_row_ids
            assert all("_row_id" not in m for m in loaded), "this probe needs a load without row ids"
            swept = [_fresh_compaction_message_copy(m) for m in loaded]
            db.append_messages_batch(SESSION_ID, swept)

            assert len(db.get_messages(SESSION_ID)) == before, (
                "the marker-swept compaction copy lost its provenance and re-appended the transcript"
            )
        finally:
            db.close()


def _transcript(base_ts=1_700_000_000.0):
    """One logical turn: user + assistant(tool_calls) + tool result. Timestamps are explicit, so a
    re-materialized copy is byte-identical (the shape that produced identical display_identity)."""
    return [
        {"role": "user", "content": "run it", "timestamp": base_ts},
        {
            "role": "assistant", "content": "", "timestamp": base_ts + 1, "finish_reason": "tool_calls",
            "tool_calls": [
                {"id": "call_real_1", "type": "function",
                 "function": {"name": "terminal", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_real_1", "content": "output", "timestamp": base_ts + 2},
    ]


def test_d_durable_copy_still_reconciles(tmp_path):
    """The other half of the contract: a dict that PROVES it is a materialization of a durable row
    (row id stamped by a previous flush, or the born-durable marker) must still reconcile — this is
    what keeps #111996 closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _db(tmpdir)
        try:
            messages = [{"role": "user", "content": "run it", "timestamp": 1_700_000_000.0},
                        {"role": "assistant", "content": "done", "timestamp": 1_700_000_001.0}]
            db.append_messages_batch(SESSION_ID, messages)
            before = len(db.get_messages(SESSION_ID))

            # Re-flush the SAME dicts: the writer stamps _row_id on them, so they are proven copies.
            db.append_messages_batch(SESSION_ID, messages)
            assert len(db.get_messages(SESSION_ID)) == before, "a proven durable copy was re-inserted"

            # And a reload without row ids is proven durable by the born-durable marker.
            loaded = db.get_messages_as_conversation(SESSION_ID)
            db.append_messages_batch(SESSION_ID, loaded)
            assert len(db.get_messages(SESSION_ID)) == before, (
                "a born-durable materialization was re-inserted as a physical copy")
        finally:
            db.close()
