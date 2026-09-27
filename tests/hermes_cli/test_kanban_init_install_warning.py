"""Regression tests for issue #87283 — warn about gateway auto-dispatch at
install time, from *every* entry point that creates state.

``hermes kanban init`` is the install-time command, but it is not the only
way to land on a fresh ``HERMES_HOME``: ``kanban_command()`` auto-inits the
DB before dispatching *any* subcommand, so ``hermes kanban create …`` (and
``boards create``) create the board DB too. Warning only in ``_cmd_init``
left the hazard the issue is about reachable one command earlier, with no
notice at all.

Two properties these tests depend on:

* The guard phrases below are quoted from the post-fix warning and are
  unique to it. The pre-fix output already contains "gateway" and other
  broad keywords, so an ``or``-joined keyword set passes against the
  unfixed code and guards nothing.
* The warning is written to stderr, so stdout — and therefore ``--json`` —
  stays machine-parseable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb


# Phrases that only the post-fix warning contains.
NO_CAP_PHRASE = "no global cap on"                        # the missing global cap
HARD_DEP_PHRASE = "install is non-functional without it"  # gateway is required


@pytest.fixture
def fresh_home(tmp_path, monkeypatch):
    """A bare HERMES_HOME with no kanban state and no env-marker that would
    mark us as a delegate child (init/create are denied in that context, see
    `_is_delegated_child_cli_mutation`)."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for var in (
        "HERMES_DELEGATED_CHILD_CONTEXT",
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_HOME",
        "HERMES_KANBAN_BOARD",
    ):
        monkeypatch.delenv(var, raising=False)
    # hermes_constants caches the resolved root; kanban_db caches init by path.
    try:
        import hermes_constants
        hermes_constants._cached_default_hermes_root = None  # type: ignore[attr-defined]
    except Exception:
        pass
    kb._INITIALIZED_PATHS.clear()
    return home


def _assert_warned(out: str) -> None:
    assert NO_CAP_PHRASE in out.lower(), (
        "the install-time dispatch warning (no global concurrency cap) was "
        "not shown; got:\n" + out
    )


# ---------------------------------------------------------------------------
# init — the install-time command
# ---------------------------------------------------------------------------


def test_kanban_init_warns_gateway_is_hard_dependency(fresh_home):
    """The install-time command must not present the gateway as an optional
    "next step": starting it is the moment every 'ready' card starts being
    dispatched."""
    out = kc.run_slash("init")
    _assert_warned(out)
    assert HARD_DEP_PHRASE in out.lower(), out
    assert "opt-in" in out.lower(), out


def test_kanban_init_warns_no_global_concurrency_cap(fresh_home):
    """Even before any cards exist the user must be told the dispatcher does
    not cap concurrent workers."""
    out = kc.run_slash("init")
    _assert_warned(out)


# ---------------------------------------------------------------------------
# The same notice from the other entry points that create state
# ---------------------------------------------------------------------------


def test_kanban_daemon_on_fresh_home_warns(fresh_home):
    """`daemon` is the other surface that boots a dispatcher and it reaches
    `kb.init_db()` through the same auto-init, so a fresh HERMES_HOME gets
    the notice too. Without `--force` it returns 2 with the deprecation text
    instead of running the loop."""
    out = kc.run_slash("daemon")
    _assert_warned(out)


def test_kanban_create_on_fresh_home_warns(fresh_home):
    """`kanban create` auto-inits the DB before creating the card, so on a
    fresh HERMES_HOME it is the command that brings the board into
    existence — it must carry the same warning as `init`."""
    out = kc.run_slash("create 'remove orphan containers'")
    assert "Created " in out, out
    _assert_warned(out)


def test_kanban_boards_create_on_fresh_home_warns(fresh_home):
    """`boards create` reaches a fresh HERMES_HOME without `kanban init` and
    creates the board DB."""
    out = kc.run_slash("boards create beta")
    assert "beta" in out, out
    _assert_warned(out)


def test_warning_is_not_repeated_once_state_exists(fresh_home):
    """The notice belongs to the call that creates state; repeating a
    15-line block on every later command would be noise."""
    first = kc.run_slash("create 'first card'")
    _assert_warned(first)
    second = kc.run_slash("list")
    assert NO_CAP_PHRASE not in second.lower(), second


def test_json_stdout_stays_machine_parseable_on_fresh_home(fresh_home, capsys):
    """The warning goes to stderr: a fresh `kanban list --json` must still
    emit pristine JSON on stdout for callers that parse it."""
    parser = argparse.ArgumentParser(prog="hermes", add_help=False)
    kc.build_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["kanban", "list", "--json"])
    assert kc.kanban_command(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    _assert_warned(captured.err)


# ---------------------------------------------------------------------------
# The shared helper itself
# ---------------------------------------------------------------------------


def test_install_time_warning_helper_is_reusable():
    """One helper owns the wording; call sites must not copy-paste it."""
    helper = getattr(kc, "_install_time_dispatch_warning", None)
    assert callable(helper), "kanban.py must expose _install_time_dispatch_warning()"
    msg = helper()
    assert isinstance(msg, str) and msg.strip()
    for phrase in (NO_CAP_PHRASE, HARD_DEP_PHRASE, "gateway", "kanban.dispatch_in_gateway"):
        assert phrase in msg.lower(), f"warning text lost {phrase!r}: {msg}"


def test_warn_new_state_fires_only_before_the_db_exists(fresh_home, capsys):
    """The shared guard is what every state-creating entry point calls; it
    reports whether it printed so callers can avoid double-printing."""
    capsys.readouterr()
    assert kc._warn_new_state() is True
    assert NO_CAP_PHRASE in capsys.readouterr().err.lower()
    kb.init_db()
    assert kc._warn_new_state() is False
    assert NO_CAP_PHRASE not in capsys.readouterr().err.lower()
