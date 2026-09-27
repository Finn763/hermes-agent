"""Regression tests for #6702: `hermes update --no-restart` opt-out.

When `hermes update` runs INSIDE the gateway's own process tree (the
hermes-auto-update cron job, an in-gateway scheduled task, etc.), the
auto-restart phase SIGTERMs the gateway and kills the worker thread
mid-flight, so the cron session never gets to write its bookkeeping.

The fix: an explicit `--no-restart` flag that defers the restart
(`hermes gateway restart` runs it on the operator's schedule).

Two layers of tests:

  1. **Parser** — ``--no-restart`` is registered on the extracted parser
     with ``store_true`` and a False default. This is the RED that fails
     on unfixed code (argparse rejects unknown flags).
  2. **Behavior** — the ``_no_restart_guard`` helper short-circuits the
     inlined restart phase when the flag is set; default behavior is
     unchanged.

The guard is extracted as a tiny pure helper so it can be tested in
isolation without dragging the whole ``_cmd_update_impl`` body along.
"""

import argparse
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class TestParserExposesNoRestart:
    """The extracted update parser must declare ``--no-restart``."""

    def test_parser_accepts_no_restart_flag(self):
        """`build_update_parser` registers ``--no-restart`` as store_true."""
        from hermes_cli.subcommands.update import build_update_parser

        top = argparse.ArgumentParser()
        sub = top.add_subparsers(dest="cmd")
        # cmd_update is only used as the func= callback; pass a no-op.
        build_update_parser(sub, cmd_update=lambda _a: None)

        parsed = top.parse_args(["update", "--no-restart"])
        assert parsed.no_restart is True

    def test_parser_default_is_false(self):
        """Without the flag, ``no_restart`` is False (no behavior change)."""
        from hermes_cli.subcommands.update import build_update_parser

        top = argparse.ArgumentParser()
        sub = top.add_subparsers(dest="cmd")
        build_update_parser(sub, cmd_update=lambda _a: None)

        parsed = top.parse_args(["update"])
        assert parsed.no_restart is False


class TestNoRestartGuard:
    """The `--no-restart` short-circuit lives in a tiny pure helper.

    Extracting it keeps the regression test independent of the
    7000+-line ``_cmd_update_impl`` body, and gives the inlined call
    site one named thing to wire up.
    """

    def test_guard_returns_true_when_flag_is_set(self, capsys):
        """`_no_restart_guard(args)` returns True when ``no_restart`` is True."""
        from hermes_cli.update_cmd import _no_restart_guard

        args = SimpleNamespace(no_restart=True)
        # `windows_token` is a real dict that the guard flips; we pass a
        # marker so we can also verify it was mutated.
        token = {"resume_needed": True}
        result = _no_restart_guard(args, windows_token=token)

        assert result is True
        # The hint must reach stdout.
        out = capsys.readouterr().out
        assert "Skipping gateway restart" in out
        assert "hermes gateway restart" in out
        # The Windows token must be flipped so the atexit resume no-ops.
        assert token["resume_needed"] is False

    def test_guard_returns_false_when_flag_is_unset(self, capsys):
        """`_no_restart_guard(args)` returns False when ``no_restart`` is False.

        Regression guard: a careless fix that always short-circuits
        would silently strand the fleet on pre-update code. The default
        branch must keep reaching the inlined restart block.
        """
        from hermes_cli.update_cmd import _no_restart_guard

        args = SimpleNamespace(no_restart=False)
        result = _no_restart_guard(args, windows_token=None)

        assert result is False
        # No hint when the flag is off.
        assert "Skipping gateway restart" not in capsys.readouterr().out

    def test_guard_returns_false_when_attr_missing(self):
        """Old callers without ``no_restart`` on the namespace must not break."""
        from hermes_cli.update_cmd import _no_restart_guard

        args = SimpleNamespace()  # no no_restart attribute at all
        result = _no_restart_guard(args, windows_token=None)
        assert result is False


class TestNoRestartGuardHonorsWindowsToken:
    """When a Windows pause token is passed, the guard must flip it.

    Otherwise a paused Windows gateway would come back up on PRE-update
    code the moment `hermes update` exits, hiding the deferred-restart
    contract the user just asked for.
    """

    def test_resume_needed_flag_is_set_to_false(self):
        from hermes_cli.update_cmd import _no_restart_guard

        token = {"resume_needed": True, "profiles": {"default": 12345}}
        _no_restart_guard(SimpleNamespace(no_restart=True), windows_token=token)
        assert token["resume_needed"] is False

    def test_none_token_is_safe(self, capsys):
        """On non-Windows hosts the token is None; the guard must not crash."""
        from hermes_cli.update_cmd import _no_restart_guard

        # Should not raise.
        result = _no_restart_guard(
            SimpleNamespace(no_restart=True), windows_token=None
        )
        assert result is True
        assert "Skipping gateway restart" in capsys.readouterr().out