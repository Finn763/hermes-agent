"""Pre-suffix gateway launcher cleanup (#116157).

``get_task_name()`` yields the bare ``Hermes_Gateway`` while HERMES_HOME is this platform's default and
``Hermes_Gateway_<suffix>`` otherwise, so a launcher installed in the bare era sits under a name none of
the name-keyed accessors look at: never queried, never rewritten, never removed, never reported — while
it keeps launching the gateway at every logon. ``status`` must show it, ``uninstall`` must remove it.

Every check runs against a tmp HERMES_HOME / Startup dir with ``_exec_schtasks`` (the genuine external
boundary) faked: no real Scheduled Task is queried, created or deleted.
"""

import pytest

import hermes_cli.gateway_windows as gateway_windows

CURRENT = "Hermes_Gateway_alice"
LEGACY = "Hermes_Gateway"


class FakeSchtasks:
    """Records every schtasks argv; ``registered`` answers /Query and shrinks on /Delete."""

    def __init__(self, registered=()):
        self.registered = set(registered)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args):
        self.calls.append(tuple(args))
        verb = args[0]
        if verb not in ("/Query", "/Delete"):
            raise AssertionError(f"unexpected schtasks args: {args}")
        name = args[args.index("/TN") + 1]
        if verb == "/Query":
            if "/XML" in args or name not in self.registered:
                return (1, "", "ERROR: The system cannot find the file specified.")
            return (0, f"TaskName: {name}", "")
        self.registered.discard(name)
        return (0, "SUCCESS", "")

    def deletions(self) -> list[str]:
        return [call[call.index("/TN") + 1] for call in self.calls if call[0] == "/Delete"]

    def queries(self) -> list[str]:
        return [call[call.index("/TN") + 1] for call in self.calls if call[0] == "/Query"]


@pytest.fixture
def win_env(monkeypatch, tmp_path):
    """Name-keyed Windows module wired to tmp dirs and a faked schtasks."""
    home, startup = tmp_path / "home", tmp_path / "Startup"
    startup.mkdir()
    monkeypatch.setattr(gateway_windows, "_assert_windows", lambda: None)
    monkeypatch.setattr(gateway_windows, "get_task_name", lambda: CURRENT)
    monkeypatch.setattr(gateway_windows, "_hermes_home", lambda: home)
    monkeypatch.setattr(gateway_windows, "_startup_dir", lambda: startup)
    monkeypatch.setattr(gateway_windows, "_gateway_pids", lambda: [])
    monkeypatch.setattr(gateway_windows, "_print_start_attestation_warning", lambda: None)
    return home, startup


def _write(paths) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stale launcher\r\n", encoding="utf-8")


def test_gateway_task_names_enumerates_the_bare_name_beside_the_current_one(monkeypatch, win_env):
    """The single enumeration source: current first, then the bare pre-suffix name — and no duplicate
    when the current name already IS the bare name."""
    assert gateway_windows.gateway_task_names() == (CURRENT, LEGACY)
    assert gateway_windows.legacy_task_names() == (LEGACY,)

    monkeypatch.setattr(gateway_windows, "get_task_name", lambda: LEGACY)
    assert gateway_windows.gateway_task_names() == (LEGACY,)
    assert gateway_windows.legacy_task_names() == ()


def test_uninstall_removes_the_pre_suffix_task_and_its_launchers(win_env, monkeypatch):
    """The bare-named Scheduled Task and every file only the bare name derives must go."""
    home, startup = win_env
    schtasks = FakeSchtasks(registered={CURRENT, LEGACY})
    monkeypatch.setattr(gateway_windows, "_exec_schtasks", schtasks)

    script_dir = home / "gateway-service"
    stale = [
        script_dir / f"{LEGACY}.cmd", script_dir / f"{LEGACY}.vbs",
        script_dir / f"{CURRENT}.cmd", script_dir / f"{CURRENT}.vbs",
        startup / f"{LEGACY}.vbs", startup / f"{LEGACY}.cmd", startup / f"{LEGACY}.tmp",
    ]
    _write(stale)

    gateway_windows.uninstall()

    assert [path for path in stale if path.exists()] == []
    # Both names were deleted — and the enumeration is what drove it, not a hardcoded literal.
    assert schtasks.deletions() == [CURRENT, LEGACY]
    assert schtasks.registered == set()


def test_uninstall_survives_a_missing_pre_suffix_name_and_is_idempotent(win_env, monkeypatch, capsys):
    """A pre-suffix name nobody ever used must not raise, must not be /Delete'd blind, and the second run
    must be a no-op."""
    home, startup = win_env
    schtasks = FakeSchtasks(registered={CURRENT})
    monkeypatch.setattr(gateway_windows, "_exec_schtasks", schtasks)

    create_dir = home / "gateway-service"
    create_dir.mkdir(parents=True, exist_ok=True)
    (create_dir / f"{CURRENT}.cmd").write_text("launcher\r\n", encoding="utf-8")

    gateway_windows.uninstall()
    gateway_windows.uninstall()

    assert schtasks.deletions() == [CURRENT]           # never a blind /Delete for the unused name
    assert LEGACY in schtasks.queries()                # ...but the bare name IS looked up
    assert not (startup / f"{LEGACY}.vbs").exists()    # nothing (re)created on the way through
    assert "still registered" not in capsys.readouterr().out


def test_status_warns_about_pre_suffix_leftovers(win_env, monkeypatch, capsys):
    """status is the only place these strays can surface — the ✓ report alone is what hid them."""
    _home, startup = win_env
    monkeypatch.setattr(gateway_windows, "_exec_schtasks", FakeSchtasks(registered={CURRENT, LEGACY}))
    _write([startup / f"{LEGACY}.vbs"])

    gateway_windows.status()

    out = capsys.readouterr().out
    assert f"✓ Scheduled Task registered: {CURRENT}" in out
    assert f"⚠ Legacy pre-suffix Scheduled Task still registered: {LEGACY}" in out
    assert f"⚠ Legacy pre-suffix gateway launcher still installed: {startup / f'{LEGACY}.vbs'}" in out
    assert "hermes gateway uninstall" in out


def test_status_says_nothing_about_pre_suffix_leftovers_when_there_are_none(win_env, monkeypatch, capsys):
    monkeypatch.setattr(gateway_windows, "_exec_schtasks", FakeSchtasks(registered={CURRENT}))

    gateway_windows.status()

    out = capsys.readouterr().out
    assert "Legacy pre-suffix" not in out
    assert f"✓ Scheduled Task registered: {CURRENT}" in out
