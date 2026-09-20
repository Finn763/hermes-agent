"""Regression tests: `hermes sessions` error paths return non-zero (SES-04).

Before this, delete/rename not-found, prune bad-arg, blank rename, and import
of a missing file all printed an error and returned exit 0 — a scripting/CI
hazard (a script pinning a bad id failed loudly via `pin` but deleting a bad
id "succeeded" silently). The subcommand dispatcher already maps an int
handler return to the process exit code; these tests pin the returns.
"""

from argparse import Namespace

import pytest

import hermes_cli.sessions_cmd as sc


def _args(action, **kw):
    base = dict(
        sessions_action=action,
        session_id=None, title=None, yes=True, source=None, path=None,
        from_source=None, dry_run=False, older_than=None, newer_than=None,
        before=None, after=None, limit=50,
    )
    base.update(kw)
    return Namespace(**base)


def test_delete_missing_returns_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    SessionDB(tmp_path / "state.db")  # initialize an empty store
    rc = sc.cmd_sessions(_args("delete", session_id="nope_xyz"))
    assert rc == 1
    assert "not found" in capsys.readouterr().out.lower()


def test_rename_missing_returns_1(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    SessionDB(tmp_path / "state.db")
    rc = sc.cmd_sessions(_args("rename", session_id="nope_xyz", title=["New"]))
    assert rc == 1


def test_import_missing_file_returns_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    rc = sc.cmd_sessions(_args("import", path=str(tmp_path / "nope.jsonl")))
    assert rc == 1
    assert "file not found" in capsys.readouterr().out.lower()


class _NonTtyStdin:
    def isatty(self):
        return False


class _TtyStdin:
    def isatty(self):
        return True


def test_confirm_prompt_refuses_non_tty_without_blocking(monkeypatch, capsys):
    """#77566: a service-inherited pipe never EOFs — input() would block forever."""
    import builtins
    import sys

    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    def _must_not_block(_prompt=""):
        raise AssertionError("input() must not be called on non-TTY stdin")

    monkeypatch.setattr(builtins, "input", _must_not_block)
    assert sc._confirm_prompt("Delete 3 session(s)? [y/N] ") is False
    assert "--yes" in capsys.readouterr().err


def test_confirm_prompt_still_prompts_on_tty(monkeypatch):
    import builtins
    import sys

    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "y")
    assert sc._confirm_prompt("Delete 3 session(s)? [y/N] ") is True
    monkeypatch.setattr(builtins, "input", lambda _prompt="": "n")
    assert sc._confirm_prompt("Delete 3 session(s)? [y/N] ") is False


def test_confirm_prompt_eof_still_aborts_on_tty(monkeypatch):
    import builtins
    import sys

    monkeypatch.setattr(sys, "stdin", _TtyStdin())

    def _raise_eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", _raise_eof)
    assert sc._confirm_prompt("Delete 3 session(s)? [y/N] ") is False


def test_confirm_prompt_without_stdin_refuses_instead_of_raising(
    monkeypatch, capsys
):
    """A service that spawns the child with no inherited stdin handle leaves
    sys.stdin None; .isatty() on it would raise AttributeError."""
    import sys

    monkeypatch.setattr(sys, "stdin", None)
    assert sc._confirm_prompt("Delete 3 session(s)? [y/N] ") is False
    assert "--yes" in capsys.readouterr().err


def test_optimize_storage_refuses_non_tty_without_hanging(monkeypatch, capsys):
    """`sessions optimize-storage` had its own bare input(): on a service-inherited
    pipe (never any data, never EOF) it blocked forever while the other
    confirmation prompts refused."""
    import builtins
    import sys

    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())
    # Reach the confirmation at all: the command short-circuits (and never
    # prompts) when the store is already compact.
    from hermes_state import SessionDB
    monkeypatch.setattr(SessionDB, "fts_optimize_available", lambda self: True)


    def _must_not_block(_prompt=""):
        raise AssertionError("input() must not be called on non-TTY stdin")

    monkeypatch.setattr(builtins, "input", _must_not_block)
    sc.cmd_sessions(_args("optimize-storage", yes=False))
    out, err = capsys.readouterr()
    assert "Cancelled." in out
    assert "Optimizing search-index storage" not in out  # nothing ran
    assert "--yes" in err


def test_delete_refusal_advises_only_flags_delete_defines(monkeypatch, capsys):
    """`sessions delete` accepts --yes but no --dry-run: the non-TTY refusal must
    not send the user to a flag argparse rejects with exit 2."""
    import builtins
    import sys

    from hermes_state import SessionDB
    db = SessionDB()  # the store cmd_sessions opens (tests/conftest rewires it)
    db.create_session("sess_del", "cli")
    db.close()

    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())

    def _must_not_block(_prompt=""):
        raise AssertionError("input() must not be called on non-TTY stdin")

    monkeypatch.setattr(builtins, "input", _must_not_block)
    sc.cmd_sessions(_args("delete", session_id="sess_del", yes=False))
    out, err = capsys.readouterr()
    assert "Cancelled." in out
    assert "--yes" in err
    assert "--dry-run" not in err


def test_repair_routing_refusal_advises_no_nonexistent_flag(monkeypatch, capsys):
    """`repair-routing` (prompt reached via --apply) has neither --yes nor
    --dry-run; on non-TTY it must say so instead of naming flags it lacks."""
    import builtins
    import sys

    from hermes_state import SessionDB
    db = SessionDB()
    db.create_session(
        "donor_keyed", "telegram",
        user_id="u1", session_key="agent:main:telegram:dm:u1",
        chat_id="u1", chat_type="dm",
    )
    db.create_session("orphan", "telegram", user_id="u1",
                      parent_session_id="donor_keyed")
    db.append_message("orphan", "user", "hi")
    db.close()

    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())

    def _must_not_block(_prompt=""):
        raise AssertionError("input() must not be called on non-TTY stdin")

    monkeypatch.setattr(builtins, "input", _must_not_block)
    sc.cmd_sessions(_args("repair-routing", apply=True))
    out, err = capsys.readouterr()
    assert "Aborted — nothing was changed." in out
    assert "--yes" not in err
    assert "--dry-run" not in err
    assert "interactive terminal" in err

    db = SessionDB()
    assert db.get_session("orphan").get("session_key") is None  # untouched
    db.close()
