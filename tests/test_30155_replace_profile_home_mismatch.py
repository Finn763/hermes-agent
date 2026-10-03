"""RED test for issue #30155 — ``--replace`` cross-kills sibling gateways.

Scenario: two gateways share one HERMES_HOME but use different
HERMES_PROFILE values (alpha running, beta starting with --replace).
The pidfile ``{HERMES_HOME}/gateway.pid`` is home-scoped only, so the
beta starter reads alpha's PID and SIGKILLs it.

Expected (fixed): start_gateway(replace=True) refuses WITHOUT calling
terminate_pid and returns False, with a clear remediation message.
Buggy (main): terminate_pid(42) is called and the replacement proceeds.
"""

from __future__ import annotations

import pytest


def _bound_same_home_record(pid, home, start=0):
    return {
        "pid": pid,
        "kind": "hermes-gateway",
        "argv": ["python", "-m", "hermes_cli.main", "gateway", "run"],
        "start_time": start,
        "hermes_home": str(home),
    }


class _CleanExitRunner:
    def __init__(self, config):
        self.config = config
        self.should_exit_cleanly = True
        self.exit_reason = None
        self.exit_code = None
        self.adapters = {}

    async def start(self):
        return True

    async def stop(self):
        return None


@pytest.mark.asyncio
async def test_replace_refuses_when_profile_mismatches_home(monkeypatch, tmp_path):
    """HERMES_PROFILE=beta (existing profile) + HERMES_HOME=root must not kill."""
    from gateway.config import GatewayConfig

    root = tmp_path / ".hermes"
    (root / "profiles" / "beta").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("HERMES_PROFILE", "beta")
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))

    calls: list[tuple] = []
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: 42)
    monkeypatch.setattr(
        "gateway.status.terminate_pid",
        lambda pid, force=False: calls.append((pid, force)),
    )
    # Legitimate same-home bound record so the #89315 ownership gate allows;
    # only the #30155 profile/home guard may refuse.
    monkeypatch.setattr(
        "gateway.status._read_pid_record",
        lambda path=None: _bound_same_home_record(42, root),
    )
    monkeypatch.setattr(
        "gateway.status._get_process_start_time", lambda pid: 0 if pid == 42 else None
    )
    monkeypatch.setattr("gateway.status._read_process_cmdline", lambda pid: None)
    monkeypatch.setattr("gateway.status._pid_exists", lambda pid: False)
    monkeypatch.setattr("gateway.run.os.getpid", lambda: 100)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda hermes_home, mode: tmp_path)
    monkeypatch.setattr(
        "hermes_logging._add_rotating_handler", lambda *args, **kwargs: None
    )
    monkeypatch.setattr("gateway.run.GatewayRunner", _CleanExitRunner)

    from gateway.run import start_gateway

    ok = await start_gateway(config=GatewayConfig(), replace=True, verbosity=None)

    assert ok is False
    assert calls == []


class TestProfileHomeMismatch:
    """Unit tests for gateway.run._hermes_profile_home_mismatch (#30155)."""

    def _setup(self, monkeypatch, tmp_path, profile=None, home=None):
        from pathlib import Path as _Path

        monkeypatch.setattr(_Path, "home", lambda: tmp_path)
        root = tmp_path / ".hermes"
        root.mkdir(exist_ok=True)
        (root / "profiles" / "beta").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(root if home is None else home))
        if profile is None:
            monkeypatch.delenv("HERMES_PROFILE", raising=False)
        else:
            monkeypatch.setenv("HERMES_PROFILE", profile)
        return root

    def test_unset_profile_allows(self, monkeypatch, tmp_path):
        from gateway.run import _hermes_profile_home_mismatch

        self._setup(monkeypatch, tmp_path)
        assert _hermes_profile_home_mismatch() is None

    def test_matching_profile_home_allows(self, monkeypatch, tmp_path):
        from gateway.run import _hermes_profile_home_mismatch

        root = self._setup(monkeypatch, tmp_path)
        monkeypatch.setenv("HERMES_PROFILE", "beta")
        monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "beta"))
        assert _hermes_profile_home_mismatch() is None

    def test_mismatched_profile_home_refuses(self, monkeypatch, tmp_path):
        from gateway.run import _hermes_profile_home_mismatch

        self._setup(monkeypatch, tmp_path, profile="beta")
        reason = _hermes_profile_home_mismatch()
        assert reason is not None
        assert "beta" in reason and "gateway.pid" in reason

    def test_attribution_only_value_allows(self, monkeypatch, tmp_path):
        """HERMES_PROFILE as kanban author tag (no such profile) is ignored."""
        from gateway.run import _hermes_profile_home_mismatch

        self._setup(monkeypatch, tmp_path, profile="worker-bot")
        assert _hermes_profile_home_mismatch() is None

    def test_invalid_profile_name_allows(self, monkeypatch, tmp_path):
        from gateway.run import _hermes_profile_home_mismatch

        self._setup(monkeypatch, tmp_path, profile="No:Colon")
        assert _hermes_profile_home_mismatch() is None

    def test_default_profile_on_root_allows(self, monkeypatch, tmp_path):
        from gateway.run import _hermes_profile_home_mismatch

        self._setup(monkeypatch, tmp_path, profile="default")
        assert _hermes_profile_home_mismatch() is None
