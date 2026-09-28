"""Attempt cap for auto-decompose on a failing triage card (issue #118603).

Without a cap, ``auto_decompose_tick`` retries a card whose decomposition
fails on *every* dispatcher tick, forever — burning dispatcher budget and
(reported) re-billing one aux LLM call per tick with no cost attribution.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from gateway import kanban_watchers_dispatcher as kwd


def _dispatcher():
    settings = kwd._DispatcherSettings(60.0, None, None, 2, 0, True, None, None)
    return kwd._KanbanDispatcher(SimpleNamespace(DEFAULT_BOARD="default"), settings)


def test_failing_triage_card_is_not_retried_every_tick_forever(monkeypatch):
    """A card that fails 8 straight ticks must be billed at most 3 times."""
    monkeypatch.setattr(kwd, "_board_slugs", lambda kb: ["default"])
    bills: list[str] = []

    def fake_decompose(task_id, author=None):
        bills.append(task_id)
        return SimpleNamespace(ok=False, fanout=False, child_ids=None,
                               reason="LLM error: InternalServerError")

    fake = SimpleNamespace(list_triage_ids=lambda: ["t_doomed"], decompose_task=fake_decompose)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_decompose", fake)

    dispatcher = _dispatcher()
    for _ in range(8):
        dispatcher.auto_decompose_tick(10)

    assert len(bills) <= 3, (
        f"doomed card billed {len(bills)} times over 8 dispatcher ticks — "
        "no attempt cap (issue #118603)"
    )


def test_transient_failure_recovers_after_retry_window(monkeypatch):
    """A card parked at the cap is retried once the window elapses, so a
    transient upstream outage doesn't starve it until restart (#118603 review)."""
    monkeypatch.setattr(kwd, "_board_slugs", lambda kb: ["default"])
    now = [1_000_000.0]
    monkeypatch.setattr(kwd.time, "monotonic", lambda: now[0])

    upstream_ok = {"v": False}

    def fake_decompose(task_id, author=None):
        if upstream_ok["v"]:
            return SimpleNamespace(ok=True, fanout=False, child_ids=None, reason=None)
        return SimpleNamespace(ok=False, fanout=False, child_ids=None,
                               reason="LLM error: APIConnectionError")

    fake = SimpleNamespace(list_triage_ids=lambda: ["t_tmp"], decompose_task=fake_decompose)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_decompose", fake)

    dispatcher = _dispatcher()
    for _ in range(3):
        dispatcher.auto_decompose_tick(10)
    assert dispatcher._decompose_failures[("default", "t_tmp")][0] == 3

    # Upstream recovers, but the card stays parked inside the window.
    upstream_ok["v"] = True
    for _ in range(5):
        assert dispatcher.auto_decompose_tick(10) == 0

    # Once the window elapses the card is retried and decomposes.
    now[0] += dispatcher._DECOMPOSE_RETRY_WINDOW_SECONDS + 1
    assert dispatcher.auto_decompose_tick(10) == 1


def test_list_read_failure_keeps_the_failure_budgets(monkeypatch):
    """A transient list_triage_ids() failure must not read as "all cards left
    triage" and wipe the whole board's budgets (#118603 review)."""
    monkeypatch.setattr(kwd, "_board_slugs", lambda kb: ["default"])
    list_fails = {"v": False}

    def fake_list():
        if list_fails["v"]:
            raise RuntimeError("database is locked")
        return ["t_card"]

    def fake_decompose(task_id, author=None):
        return SimpleNamespace(ok=False, fanout=False, child_ids=None, reason="boom")

    fake = SimpleNamespace(list_triage_ids=fake_list, decompose_task=fake_decompose)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_decompose", fake)

    dispatcher = _dispatcher()
    for _ in range(2):
        dispatcher.auto_decompose_tick(10)
    assert dispatcher._decompose_failures[("default", "t_card")][0] == 2

    list_fails["v"] = True
    dispatcher.auto_decompose_tick(10)  # listing raises this tick
    assert dispatcher._decompose_failures[("default", "t_card")][0] == 2, (
        "a failed list read reset the board's decompose budgets"
    )
