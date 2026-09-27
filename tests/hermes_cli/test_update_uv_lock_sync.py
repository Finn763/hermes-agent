"""hermes update must reconcile the venv with uv.lock so `uv run hermes` does
not re-resolve dependencies on the next launch (#8744).

Reproducer: after `hermes update` finishes its editable install, the venv
can drift from uv.lock because `uv pip install -e .[all]` does not enforce
lockfile pinning. The next `uv run hermes` launch then has to re-validate,
which is when the user sees a network round-trip for git-pinned extras like
tinker / yc-bench / atropos, and an offline launch dies with "Could not
resolve host: github.com".

The fix: when uv is the install tool, finish the update by running
`uv sync --extra all --locked` against the same project root. That sync is
the single source of truth for what the venv should contain, and a
following `uv run hermes` validates against the already-synced venv with no
network calls. Falling back to the legacy `uv pip install -e .[all]` path
when `uv.lock` is missing keeps ZIP-swap and bare-checkout installs working
the way they did before.
"""

import subprocess
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """A temp project with pyproject + uv.lock, subprocess.run recorded.

    Skip the optional-extras loop entirely (it spawns N more processes and
    we only want to assert on the post-install `uv sync`). Stub
    managed_uv helpers so the install path sees a real uv and goes through
    the uv branch.
    """
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "hermes-agent"\nversion = "0"\ndependencies = []\n'
    )
    (project / "uv.lock").write_text("# fake lockfile\n")
    # The install path's VIRTUAL_ENV guard (#71510) drops VIRTUAL_ENV when
    # the pointed-to venv directory does not exist. Create the fake venv
    # so the happy-path env passes through to subprocess.run.
    (project / "venv").mkdir()

    from hermes_cli import main as hermes_main

    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", project)
    monkeypatch.setattr(hermes_main, "_is_windows", lambda: False)
    monkeypatch.setattr(hermes_main, "_venv_scripts_dir", lambda: None)
    monkeypatch.setattr(
        hermes_main, "_load_installable_optional_extras", lambda group="all": []
    )

    fake_uv = "C:/fake/uv.exe"
    monkeypatch.setattr(
        "shutil.which", lambda name: fake_uv if name == "uv" else None
    )
    for fn in (
        "resolve_uv",
        "ensure_uv",
        "update_managed_uv",
        "managed_python_env",
    ):
        if fn in ("resolve_uv", "ensure_uv"):
            val = lambda **_kw: fake_uv
        elif fn == "update_managed_uv":
            val = lambda **_kw: None
        else:
            val = lambda: {}
        monkeypatch.setattr(
            "hermes_cli.managed_uv." + fn, val, raising=False
        )

    log: list[dict] = []

    def _run(cmd, *args, **kwargs):
        log.append(
            {"cmd": list(cmd) if isinstance(cmd, (list, tuple)) else [cmd], **kwargs}
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    return project, fake_uv, log


def test_uv_run_hermes_no_longer_re_resolves_after_update(_isolate):
    """After #8744, `hermes update` must finish with `uv sync --extra all
    --locked` so the next `uv run hermes` validates against an already-synced
    venv and does no network round-trips for git-pinned extras.
    """
    project, fake_uv, log = _isolate
    from hermes_cli import main as hermes_main

    hermes_main._install_python_dependencies_with_optional_fallback(
        [fake_uv, "pip"],
        env={"VIRTUAL_ENV": str(project / "venv")},
        group="all",
    )

    lockfile_synced = any(
        {"sync", "--locked", "--extra", "all"}.issubset(set(c["cmd"]))
        or {"sync", "--locked", "--all-extras"}.issubset(set(c["cmd"]))
        for c in log
    )
    assert lockfile_synced, (
        "after #8744, `hermes update` must finish with `uv sync --extra all "
        "--locked` so the next `uv run hermes` does not re-resolve deps. "
        f"Recorded commands: {[c['cmd'] for c in log]}"
    )


def test_lockfile_sync_targets_the_venv_hermes_runs_from(_isolate):
    """The `uv sync` must run with cwd=PROJECT_ROOT and be *pointed at* the venv
    the pip install just wrote to.

    `uv sync` ignores a VIRTUAL_ENV that does not match the project environment
    and creates/updates PROJECT_ROOT/.venv instead, so propagating VIRTUAL_ENV
    alone reconciles the wrong environment (#8744 review). Assert the sync is
    steered at `venv/` explicitly.
    """
    project, fake_uv, log = _isolate
    from hermes_cli import main as hermes_main

    venv = str(project / "venv")
    hermes_main._install_python_dependencies_with_optional_fallback(
        [fake_uv, "pip"], env={"VIRTUAL_ENV": venv}, group="all"
    )

    sync_call = next((c for c in log if "sync" in c["cmd"]), None)
    assert sync_call is not None, (
        "expected a `uv sync` subprocess call; got: "
        f"{[c['cmd'] for c in log]}"
    )
    assert sync_call.get("cwd") == project, (
        f"`uv sync` must run with cwd=PROJECT_ROOT ({project}), "
        f"got {sync_call.get('cwd')!r}"
    )
    env = sync_call.get("env")
    assert env is not None, "`uv sync` must get an env carrying the target venv"
    assert env.get("UV_PROJECT_ENVIRONMENT") == venv, (
        "`uv sync` targets the project environment unless told otherwise, so it "
        f"must be pointed at {venv}; got "
        f"UV_PROJECT_ENVIRONMENT={env.get('UV_PROJECT_ENVIRONMENT')!r}"
    )
    assert env.get("VIRTUAL_ENV") == venv, (
        f"`uv sync` must propagate VIRTUAL_ENV={venv}, "
        f"got {env.get('VIRTUAL_ENV')!r}"
    )


def test_lockfile_sync_uses_the_callers_extra_group(_isolate):
    """A Termux caller syncs its curated `termux-all` profile, not `all`.

    The install asked for `.[termux-all]`; a hardcoded `--extra all` would pull
    the full extra set into that profile and uninstall the curated pins
    (#8744 review).
    """
    project, fake_uv, log = _isolate
    from hermes_cli import main as hermes_main

    hermes_main._install_python_dependencies_with_optional_fallback(
        [fake_uv, "pip"],
        env={"VIRTUAL_ENV": str(project / "venv")},
        group="termux-all",
    )

    sync_call = next((c for c in log if "sync" in c["cmd"]), None)
    assert sync_call is not None, (
        f"expected a `uv sync` subprocess call; got: {[c['cmd'] for c in log]}"
    )
    cmd = sync_call["cmd"]
    extras = [cmd[i + 1] for i, tok in enumerate(cmd[:-1]) if tok == "--extra"]
    assert extras == ["termux-all"], (
        "`uv sync` must request the caller's group; "
        f"got extras={extras!r} in {cmd}"
    )


def test_lockfile_sync_skipped_when_no_project_venv_exists(_isolate):
    """A pip / site-packages install has no project venv to reconcile.

    Running `uv sync` there targets PROJECT_ROOT/.venv and would strand a
    brand-new environment nothing runs, so the sync must be skipped outright
    (#8744 review).
    """
    project, fake_uv, log = _isolate
    # The caller still passes VIRTUAL_ENV=PROJECT_ROOT/venv, but on this install
    # that directory does not exist (the helper pins the interpreter instead).
    (project / "venv").rmdir()
    from hermes_cli import main as hermes_main

    hermes_main._install_python_dependencies_with_optional_fallback(
        [fake_uv, "pip"],
        env={"VIRTUAL_ENV": str(project / "venv")},
        group="all",
    )

    sync_calls = [c["cmd"] for c in log if "sync" in c["cmd"]]
    assert sync_calls == [], (
        "without a project venv there is nothing to reconcile; a `uv sync` here "
        f"only creates a stray PROJECT_ROOT/.venv. Got: {sync_calls}"
    )


def test_zip_swap_without_lockfile_skips_lockfile_sync(_isolate):
    """A bare checkout (no uv.lock) must not crash trying to lockfile-sync —
    fall back to the legacy pip-only path. Keeps ZIP-swap and minimal
    installs working.
    """
    project, fake_uv, log = _isolate
    # Remove the fake lockfile to simulate a non-uv checkout.
    (project / "uv.lock").unlink()
    from hermes_cli import main as hermes_main

    hermes_main._install_python_dependencies_with_optional_fallback(
        [fake_uv, "pip"], env={"VIRTUAL_ENV": str(project / "venv")}, group="all"
    )

    sync_calls = [c for c in log if "sync" in c["cmd"]]
    assert sync_calls == [], (
        "without uv.lock, the lockfile-sync must be skipped (uv would refuse "
        f"--locked). Got: {[c['cmd'] for c in sync_calls]}"
    )