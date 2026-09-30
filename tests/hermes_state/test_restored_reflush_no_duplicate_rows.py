"""Behaviour contract for the shared batch write path: re-flushing an already-persisted
transcript block must not create a SECOND row per logical message.

The flush dedupes per dict via ``_DB_PERSISTED_MARKER``; a durable row is addressed for a
payload rewrite via ``_row_id``. ``_row_id`` is OPT-IN on restore
(``SessionDB.get_messages_as_conversation(include_row_ids=...)``), so a restored transcript
legitimately arrives with no row address. Every site that rewrites such a dict in place pops
the marker so the flush re-writes it (``agent_runtime_helpers`` sequence repair, micro
defrag/merge, tool-arg sanitizer). With no row address the shared repair step has nothing to
match and falls through to a FRESH INSERT: the whole block lands a second time as new rows
with new ids/message_uids and the original timestamps (Hermes #129065, desktop long sessions:
a 163-row history block stored twice, timestamp jumping backwards mid-session).

The invariant asserted here is relational: what the writer is handed and what a reader can
get back must describe the same number of logical messages.
"""

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    store = SessionDB(tmp_path / "state.db")
    yield store
    store.close()


def _block(n_turns: int) -> list:
    """A contiguous history block with original timestamps."""
    from agent.message_metadata import stamp_message_uid

    block = []
    for i in range(n_turns):
        user = {"role": "user", "content": f"question {i}", "timestamp": 1000.0 + i * 10}
        assistant = {"role": "assistant", "content": f"answer {i}", "timestamp": 1001.0 + i * 10}
        for msg in (user, assistant):
            stamp_message_uid(msg)
            block.append(msg)
    return block


def _logical_rows(store, session_id) -> int:
    """How many logical messages a reader gets back (one row per content)."""
    rows = store.get_messages(session_id)
    return len({row["content"] for row in rows})


def test_reflushed_restored_block_does_not_duplicate_rows(db):
    """A restored block re-flushed without row addresses must not double the stored transcript.

    Mirrors the real handoff: restore (``include_row_ids`` off), then a rewrite that pops the
    persistence marker so the next flush re-persists the dict.
    """
    session_id = "session-restore-reflush"
    db.create_session(session_id=session_id, source="test")

    block = _block(6)
    db.append_messages_batch(session_id, block)

    durable_before = db.message_count(session_id)
    assert durable_before == len(block)

    # Restore WITHOUT row addresses: the opt-in shape a resume surface gets.
    restored = db.get_messages_as_conversation(session_id)
    assert restored, "restore returned nothing"
    assert all("_row_id" not in msg for msg in restored), "restore unexpectedly carried row ids"

    # An in-place rewrite of a restored dict pops the marker so the flush re-writes the row
    # (the contract every marker-pop site in agent/ relies on).
    from agent.context_compressor import _DB_PERSISTED_MARKER

    for msg in restored:
        msg.pop(_DB_PERSISTED_MARKER, None)

    db.append_messages_batch(session_id, restored)

    assert db.message_count(session_id) == durable_before, (
        "re-flushing an already-persisted block appended a second row per message; "
        "the stored transcript doubled"
    )
    assert _logical_rows(db, session_id) == len(block), (
        "a reader no longer sees exactly one row per logical message"
    )


def test_reflushed_rows_keep_their_durable_identity(db):
    """The adopted rows must be the ORIGINAL ones: same ids, same message_uids.

    Pins the relational half of the same contract - a re-flush re-persists, it does not
    re-issue identity.
    """
    session_id = "session-restore-identity"
    db.create_session(session_id=session_id, source="test")

    block = _block(4)
    db.append_messages_batch(session_id, block)

    before = {
        row["content"]: (row["id"], row["message_uid"])
        for row in db.get_messages(session_id)
    }

    restored = db.get_messages_as_conversation(session_id)

    from agent.context_compressor import _DB_PERSISTED_MARKER

    for msg in restored:
        msg.pop(_DB_PERSISTED_MARKER, None)

    db.append_messages_batch(session_id, restored)

    after = {row["content"]: (row["id"], row["message_uid"]) for row in db.get_messages(session_id)}
    assert after == before, "re-flush re-issued row ids / message_uids for already-durable messages"