"""Regression (#126021): in-place compaction must not resurrect rows on flush.

Root cause: ``compress()``'s terminal sweep (``_strip_persistence_markers``)
removes ``_db_persisted`` from the assembled list, and ``archive_and_compact``
inserted those dicts as the new live set WITHOUT re-stamping them. The next
identity-losing flush (incremental tool-call persist with no
``conversation_history`` arg, or a second agent instance on a multiplexed
gateway) treated every compacted dict as new and re-INSERTed it as a duplicate
active row — byte-identical content AND identical microsecond timestamps,
because explicit ``timestamp`` values are preserved on write.

Fix: ``archive_and_compact`` stamps its inserted dicts durable at the source
(they are live rows under the same session id). Rotation is untouched: it
publishes via ``publish_compression_child`` and must stay unstamped (#57491).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hermes_state import SessionDB, _DB_PERSISTED_MARKER_KEY
from run_agent import AIAgent


def _make_flush_agent(db: SessionDB, session_id: str):
    """Minimal agent shell that owns the real flush implementation."""
    agent = SimpleNamespace(
        _session_db=db,
        _session_db_created=True,
        _persist_disabled=False,
        session_id=session_id,
        _session_persist_lock=None,
        _flushed_db_message_ids=set(),
        _flushed_db_message_session_id=None,
        _last_flushed_db_idx=0,
        _persist_user_message_idx=None,
        _persist_user_message_override=None,
        _persist_user_message_timestamp=None,
        _pending_cli_user_message=None,
    )
    agent._ensure_db_session = lambda: None
    agent._flush_messages_to_session_db = (
        AIAgent._flush_messages_to_session_db.__get__(agent, AIAgent)
    )
    agent._flush_messages_to_session_db_unlocked = (
        AIAgent._flush_messages_to_session_db_unlocked.__get__(agent, AIAgent)
    )
    return agent


def _seed_session(db: SessionDB, sid: str, turns: int = 3) -> None:
    db.create_session(sid, source="cli")
    for i in range(turns):
        db.append_message(sid, "user", f"question {i}")
        db.append_message(sid, "assistant", f"answer {i}")


def _live_turn(db: SessionDB, sid: str) -> list:
    """Load transcript, append one fresh turn, flush it (stamps everything)."""
    live = db.get_messages_as_conversation(sid)
    live.append({"role": "user", "content": "current question"})
    live.append({"role": "assistant", "content": "current answer"})
    agent = _make_flush_agent(db, sid)
    agent._flush_messages_to_session_db(live)
    return live


def _compact_in_place(db: SessionDB, sid: str, live: list) -> list:
    """Production in-place commit sequence: sweep + archive, like compress()."""
    from agent.context_compressor import _strip_persistence_markers

    stripped = [dict(m) for m in live]
    _strip_persistence_markers(stripped)
    db.archive_and_compact(sid, stripped)
    return stripped


def _active_contents(db: SessionDB, sid: str) -> list:
    return [m.get("content") for m in db.get_messages(sid)]


def test_archive_and_compact_stamps_inserted_rows(tmp_path: Path) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    _seed_session(db, "S1")
    live = _live_turn(db, "S1")

    stripped = _compact_in_place(db, "S1", live)
    assert all(
        m.get(_DB_PERSISTED_MARKER_KEY) is True for m in stripped if isinstance(m, dict)
    )


def test_identityless_flush_after_inplace_compact_keeps_count_flat(
    tmp_path: Path,
) -> None:
    """The #126021 shape: compact in place, then flush with no history arg.

    Pre-fix this re-INSERTed the whole compacted set (row count doubles, the
    new rows carrying byte-identical content and identical microsecond
    timestamps). Post-fix the count stays flat.
    """
    db = SessionDB(db_path=tmp_path / "state.db")
    _seed_session(db, "S2", turns=2)
    live = _live_turn(db, "S2")
    baseline = _active_contents(db, "S2")

    stripped = _compact_in_place(db, "S2", live)
    assert _active_contents(db, "S2") == baseline

    # Fresh agent, no identity state, no conversation_history boundary: the
    # incremental tool-progress persist / multiplexed second-agent shape.
    agent = _make_flush_agent(db, "S2")
    agent._flush_messages_to_session_db(stripped)

    assert _active_contents(db, "S2") == baseline


def test_new_tail_after_compact_still_flushes(tmp_path: Path) -> None:
    """Guard against over-skipping: only compacted rows are exempt."""
    db = SessionDB(db_path=tmp_path / "state.db")
    _seed_session(db, "S3", turns=1)
    live = _live_turn(db, "S3")
    baseline = _active_contents(db, "S3")

    stripped = _compact_in_place(db, "S3", live)

    agent = _make_flush_agent(db, "S3")
    agent._flush_messages_to_session_db(stripped)
    assert _active_contents(db, "S3") == baseline

    stripped.append({"role": "user", "content": "next question"})
    stripped.append({"role": "assistant", "content": "next answer"})
    agent._flush_messages_to_session_db(stripped)
    assert _active_contents(db, "S3") == baseline + ["next question", "next answer"]
