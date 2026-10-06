"""#122691: `hermes update` emits a determinate phase progress sequence.

The update pipeline printed one bare arrow line per phase with no sense of how
much download/install work remained before the app restarted (#122691). These
tests encode the requested behavior: a progress callback fires across the
update phases (fetch -> prepare -> pull -> install) with a monotonic
``[k/N]`` sequence, and byte-level progress is reported when a transport
exposes sizes.
"""

import argparse
import re
import subprocess
from types import SimpleNamespace

import pytest

from hermes_cli import main, update_cmd
from hermes_cli.subcommands.update import build_update_parser


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True,
        text=True, stdin=subprocess.DEVNULL).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A local git install one commit behind origin/main, ready for `hermes update`."""
    home, origin, root = (tmp_path / name for name in ("home", "origin", "checkout"))
    home.mkdir()
    origin.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_INSTALL_ROOT", str(root))
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    git(origin, "init", "-b", "main")
    git(origin, "config", "user.name", "Progress Fixture")
    git(origin, "config", "user.email", "fixture@example.invalid")
    git(origin, "config", "commit.gpgsign", "false")
    (origin / "content.txt").write_text("installed", encoding="utf-8")
    git(origin, "add", "content.txt")
    git(origin, "commit", "-m", "installed")
    first = git(origin, "rev-parse", "HEAD")
    (origin / "content.txt").write_text("published", encoding="utf-8")
    git(origin, "add", "content.txt")
    git(origin, "commit", "-m", "published")
    git(tmp_path, "clone", str(origin), str(root))
    # Local main sits one commit behind origin/main so the update has real work.
    git(root, "reset", "--hard", first)
    monkeypatch.setattr(main, "PROJECT_ROOT", root)
    monkeypatch.setattr("hermes_cli.config.get_project_root", lambda: root)
    parser = argparse.ArgumentParser()
    build_update_parser(parser.add_subparsers(), cmd_update=main.cmd_update)
    opts = update_cmd._UpdateOptions(
        pre_update_version=None, gw_input_fn=None, assume_yes=True, keep_stash=False,
        switch_branch=False, discard_local_changes=False)
    monkeypatch.setattr(update_cmd, "_resolve_update_options", lambda *_: opts)
    monkeypatch.setattr(update_cmd, "_begin_update_receipt_and_plan", lambda *_: None)
    monkeypatch.setattr(main, "_run_pre_update_backup", lambda *_: None)
    monkeypatch.setattr(main, "_pause_windows_gateways_for_update", lambda: None)
    monkeypatch.setattr(update_cmd, "_prepare_git_command", lambda: (False, ["git"], False))
    # The install/restart phase runs in a child interpreter; capture the boundary instead.
    monkeypatch.setattr(update_cmd, "_complete_source_update", lambda request: None)
    monkeypatch.setattr(update_cmd, "_write_fleet_restart_pending_marker", lambda **kw: None)
    yield SimpleNamespace(home=home, root=root, parser=parser)


GIT_PHASES = ["Fetch updates", "Prepare checkout", "Pull update", "Install and restart"]


def test_update_emits_phase_progress_sequence(checkout, capsys):
    """A progress callback fires once per update phase, in plan order with `[k/N]`."""
    from hermes_cli import update_progress

    events = []
    update_progress.set_listener(events.append)
    try:
        args = checkout.parser.parse_args(["update", "--yes", "--branch", "main"])
        update_cmd._cmd_update_impl(args, False)
    finally:
        update_progress.set_listener(None)
        update_progress.end()

    phases = [event for event in events if event["kind"] == "phase"]
    assert [event["label"] for event in phases] == GIT_PHASES, \
        "progress callback must fire across fetch/prepare/pull/install phases"
    assert [event["index"] for event in phases] == [1, 2, 3, 4]
    assert {event["total"] for event in phases} == {4}

    out = capsys.readouterr().out
    assert "  [1/4] [#####---------------] Fetch updates" in out, out
    assert "  [4/4] [####################] Install and restart" in out, out
    assert "[2/4]" in out and "[3/4]" in out, out


def test_byte_progress_reports_transport_bytes(capsys):
    """Transports that expose sizes (ZIP urlretrieve) report byte sub-progress."""
    from hermes_cli import update_progress

    events = []
    update_progress.set_listener(events.append)
    try:
        update_progress.begin(("Download and swap update",))
        update_progress.step("Download and swap update")
        update_progress.byte_progress(50, 100)
        update_progress.byte_progress(100, 100)
    finally:
        update_progress.set_listener(None)
        update_progress.end()

    byte_events = [event for event in events if event["kind"] == "bytes"]
    assert [(event["read"], event["size"]) for event in byte_events] == [(50, 100), (100, 100)]
    out = capsys.readouterr().out
    assert "50% (50/100 bytes)" in out, out


def test_byte_progress_ignores_unknown_sizes():
    """A transport without Content-Length reports nothing instead of a bogus bar."""
    from hermes_cli import update_progress

    events = []
    update_progress.set_listener(events.append)
    try:
        update_progress.begin(("Download and swap update",))
        update_progress.step("Download and swap update")
        update_progress.byte_progress(4096, -1)
    finally:
        update_progress.set_listener(None)
        update_progress.end()
    assert [event for event in events if event["kind"] == "bytes"] == []


def test_byte_progress_continues_the_phase_slot_without_reset(capsys):
    """Review follow-up (#122691): the download's byte lines pick up the bar where
    its [k/N] step left it — the bar must never drop to empty mid-download."""
    from hermes_cli import update_progress

    update_progress.begin((
        "Resolve update channel", "Download and swap update", "Install and restart"))
    update_progress.step("Resolve update channel")
    update_progress.step("Download and swap update")
    update_progress.byte_progress(0, 4_000_000)
    update_progress.byte_progress(2_000_000, 4_000_000)
    update_progress.byte_progress(4_000_000, 4_000_000)
    update_progress.step("Install and restart")
    update_progress.end()

    out = capsys.readouterr().out
    bars = [m.group(1).count("#") for m in re.finditer(r"\[([#-]{20})\]", out)]
    assert bars == sorted(bars), bars
    assert "[2/3] [####################] 100% (4000000/4000000 bytes)" in out, out


def test_zip_fallback_replans_instead_of_stepping_out_of_plan(capsys, monkeypatch):
    """Review follow-up (#122691): a git failure that falls back to the ZIP path must
    fold the download/install phases into the active plan — the old head printed
    [4/4] before the bytes landed and [...] for a run that finished."""
    from hermes_cli import update_cmd, update_progress

    calls = []
    monkeypatch.setattr(
        update_cmd, "_should_zip_fallback_on_update_error", lambda exc: True)
    monkeypatch.setattr(
        update_cmd, "_update_via_zip", lambda *a, **k: calls.append("zip"))

    update_progress.begin(GIT_PHASES)
    update_progress.step("Fetch updates")
    update_progress.step("Prepare checkout")
    update_progress.step("Pull update")
    exc = subprocess.CalledProcessError(128, ["git", "pull", "origin", "main"])
    update_cmd._handle_update_called_process_error(
        exc, argparse.Namespace(), False, False)
    # the ZIP path's own tail (update_cmd_zip steps this after the swap)
    update_progress.step("Install and restart")
    update_progress.end()

    assert calls == ["zip"]
    out = capsys.readouterr().out
    assert "[...]" not in out, out
    assert "  [4/5] [################----] Download and swap update" in out, out
    assert "  [5/5] [####################] Install and restart" in out, out


def test_already_up_to_date_run_finishes_at_full_plan(checkout, capsys):
    """Review follow-up (#122691): the no-op path used to stop at [3/4] and leave the
    plan active — it must end at [N/N] and close the plan."""
    from hermes_cli import update_progress

    git(checkout.root, "reset", "--hard", "origin/main")
    args = checkout.parser.parse_args(["update", "--yes", "--branch", "main"])
    try:
        update_cmd._cmd_update_impl(args, False)
    finally:
        active = update_progress._ACTIVE
        update_progress.end()
    out = capsys.readouterr().out
    assert "  [4/4] [####################] Install and restart" in out, out
    assert active is None, "a finished run must not leave its plan active"


def test_channel_resolve_failure_is_marked_not_in_flight(checkout, capsys, monkeypatch):
    """Review follow-up (#122691): a channel-resolve failure used to end at a bare
    [1/5] that reads as an in-flight run; it must be marked failed and closed."""
    from hermes_cli import update_progress

    def _boom(*_a, **_k):
        raise ValueError("no channel")

    monkeypatch.setattr(
        "hermes_cli.source_releases.resolve_source_target", _boom)
    args = checkout.parser.parse_args(["update", "--yes"])
    with pytest.raises(SystemExit) as excinfo:
        update_cmd._cmd_update_impl(args, False)
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Update failed at: Resolve update channel" in out, out
    assert update_progress._ACTIVE is None


def test_begin_resets_the_byte_throttle(capsys):
    """Review follow-up (#122691): a second update in one process must not inherit the
    previous run's last_percent byte throttle."""
    from hermes_cli import update_progress

    update_progress.begin(("Download and swap update",))
    update_progress.step("Download and swap update")
    update_progress.byte_progress(100, 100)
    update_progress.begin(("Download and swap update",))
    update_progress.step("Download and swap update")
    update_progress.byte_progress(50, 100)
    update_progress.end()

    out = capsys.readouterr().out
    assert out.count("50% (50/100 bytes)") == 1, out
    assert "100% (100/100 bytes)" in out
