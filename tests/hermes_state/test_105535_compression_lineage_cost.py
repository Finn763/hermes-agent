"""#105535 — a compressed conversation's spend must follow the whole lineage.

``/compress`` rotates a conversation onto a new ``parent_session_id``-chained row. Two
cost paths kept reading the pre-rotation state:

- ``_project_compression_tips`` replaces a root's surfaced fields with its live tip's, but
  the field tuple had no cost column — so the sidebar row for a 3-segment conversation
  showed the ROOT's at-rotation snapshot while ``message_count`` on the same row was the
  tip's fresh count.
- ``usage_totals`` summed ``WHERE parent_session_id IS NULL``, a pre-compression-era
  predicate that excluded every continuation segment — so the sidebar's profile usage
  header permanently lagged true spend.

Numbers below are the ones from the issue's live store (root $0.5120 / mid $1.1730 /
tip $0.9303 = $2.6153).
"""

import time

import pytest

from hermes_state import SessionDB

ROOT_COST, MID_COST, TIP_COST = 0.5120097099, 1.1730, 0.9303
TRUE_TOTAL = ROOT_COST + MID_COST + TIP_COST


@pytest.fixture
def db(tmp_path):
    database = SessionDB(tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


def _lineage(db: SessionDB, *, billed_root: bool = False) -> None:
    """root -> mid -> tip, each segment carrying its own spend, as a real rotation leaves it."""
    base = time.time() - 3600
    db.create_session("root", source="cli")
    db.create_session("mid", source="cli", parent_session_id="root")
    db.create_session("tip", source="cli", parent_session_id="mid")
    for sid in ("root", "mid", "tip"):
        db.append_message(sid, "user", f"message in {sid}")
    db._conn.execute(
        "UPDATE sessions SET started_at=?, ended_at=?, end_reason='compression', message_count=299,"
        " estimated_cost_usd=?, actual_cost_usd=? WHERE id='root'",
        # ``None`` = never billed, the state an estimated-only segment rows in as.
        (base, base + 10, ROOT_COST, ROOT_COST if billed_root else None),
    )
    db._conn.execute(
        "UPDATE sessions SET started_at=?, ended_at=?, end_reason='compression', message_count=616,"
        " estimated_cost_usd=? WHERE id='mid'",
        (base + 20, base + 30, MID_COST),
    )
    db._conn.execute(
        "UPDATE sessions SET started_at=?, message_count=585, estimated_cost_usd=? WHERE id='tip'",
        (base + 40, TIP_COST),
    )
    db._conn.commit()


def _sidebar_rows(db: SessionDB):
    """The sidebar's exact read (recents slice of /api/profiles/sessions/sidebar)."""
    return db.list_sessions_rich(
        source="cli", exclude_sources=["tool"], limit=20, offset=0, min_message_count=1,
        include_archived=False, archived_only=False, order_by_last_active=True,
        compact_rows=True, include_pinned=True,
    )


@pytest.mark.parametrize("billed_root", [False, True])
def test_projected_row_carries_whole_lineage_spend(db: SessionDB, billed_root: bool) -> None:
    """The projected tip row shows root + mid + tip, not the root's rotation-time snapshot.

    ``billed_root`` is the shadowing case: the root's own ``actual_cost_usd`` is carried onto
    the projected row, and consumers pick the cost with ``actual or estimated``
    (``sidebar-archive.ts``, ``tui_gateway/project_tree.py``) — a nonzero root actual would
    hide the fresh lineage sum behind the frozen figure.
    """
    _lineage(db, billed_root=billed_root)

    (row,) = _sidebar_rows(db)

    assert row["_lineage_root_id"] == "root"
    assert row["message_count"] == 585, "projection must have run (tip identity surfaced)"
    assert row["estimated_cost_usd"] == pytest.approx(TRUE_TOTAL)
    assert not row["actual_cost_usd"], "stale root actual must not shadow the lineage sum"
    assert (row["actual_cost_usd"] or row["estimated_cost_usd"] or 0) == pytest.approx(TRUE_TOTAL)


def test_usage_totals_counts_post_compression_segments(db: SessionDB) -> None:
    """Profile usage header: every priced row on the account counts, continuations included."""
    _lineage(db)
    assert db.usage_totals() == {"tokens": 0, "cost_usd": pytest.approx(TRUE_TOTAL)}


def test_usage_totals_ignores_archived_lineage(db: SessionDB) -> None:
    """Archiving a conversation still removes it from the total — the lineage-wide archive
    (``set_session_archived``) covers root, mid and tip together."""
    _lineage(db)
    assert db.set_session_archived("tip", True) is True
    assert db.usage_totals()["cost_usd"] == 0.0


def test_usage_totals_keeps_the_empty_child_floor(db: SessionDB) -> None:
    """A rotation cut off before the child billed anything stays out of the total."""
    _lineage(db)
    db.create_session("child", source="cli", parent_session_id="tip")
    db._conn.execute("UPDATE sessions SET message_count=0, estimated_cost_usd=9.99 WHERE id='child'")
    db._conn.commit()
    assert db.usage_totals() == {"tokens": 0, "cost_usd": pytest.approx(TRUE_TOTAL)}
