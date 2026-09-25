"""venv_sync must work on trees where the venv does not exist yet.

It is the stdlib-only-at-import pre-venv entry point: the installers call
it on a fresh clone before any dependency is importable, and post_update
calls it after a tree swap when the venv is not trustworthy. Its
behaviour is driven through PM's public client; package resolution and
publication belong to PM, not this CLI entry point.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hermes_cli import venv_sync

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_bare(snippet: str) -> subprocess.CompletedProcess:
    program = f"import sys\nsys.path.insert(0, {str(REPO_ROOT)!r})\n" + textwrap.dedent(snippet)
    return subprocess.run([sys.executable, '-I', '-S', '-c', program],
                          capture_output=True, text=True, cwd=REPO_ROOT, timeout=120)


def test_bare_import_and_passive_paths(tmp_path):
    result = _run_bare(f"""
        from pathlib import Path
        from hermes_cli import venv_sync
        assert 'pm' not in sys.modules
        root = Path({str(tmp_path)!r})
        assert venv_sync.sync(root, check=True)['state'] == 'failed'
        (root / 'install-stamp.json').write_text('{{"updateMechanism":"external"}}')
        assert venv_sync.sync(root) == {{'state': 'sealed', 'ok': True}}
        assert venv_sync.prepare_launch(root, []) is None
        assert 'pm.install' not in sys.modules
    """)
    assert result.returncode == 0, result.stderr


def test_bare_harness_rejects_third_party_import():
    result = _run_bare('import requests')
    assert result.returncode != 0 and 'ModuleNotFoundError' in result.stderr


def _checkout(tmp_path: Path, name: str = "co") -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "uv.lock").write_text("lock-v1\n")
    return root


def _wire_pm(monkeypatch, *, current=False, error=None):
    import pm

    calls = []
    monkeypatch.setattr(pm, "venv_is_current", lambda *, project_root: current)

    def sync(*, explicit, project_root, evict_incompatible_plugins):
        assert evict_incompatible_plugins, "an update sync must disable misfit plugins, not fail"
        calls.append((project_root, explicit))
        if error:
            raise pm.InstallError("venv", error)

    monkeypatch.setattr(pm, "sync_venv", sync)
    return calls


class TestCheckoutSync:
    @pytest.mark.parametrize("foreign", [False, True])
    def test_each_root_uses_the_public_pm_transaction(self, tmp_path, monkeypatch, foreign):
        root = _checkout(tmp_path)
        monkeypatch.setattr(venv_sync, "_project_root", lambda: root)
        calls = _wire_pm(monkeypatch)
        assert venv_sync.sync(root if foreign else None) == {"state": "synced", "ok": True}
        assert calls == [(root, True)]


    def test_check_is_passive(self, tmp_path, monkeypatch):
        root = _checkout(tmp_path)
        calls = _wire_pm(monkeypatch)
        before = set(tmp_path.rglob("*"))
        assert venv_sync.sync(root, check=True) == {"state": "would-sync", "ok": True}
        assert calls == []
        assert set(tmp_path.rglob("*")) == before

    def test_a_failed_sync_is_reported_and_retried(self, tmp_path, monkeypatch):
        root = _checkout(tmp_path)
        calls = _wire_pm(monkeypatch, error="resolution failed")
        for _ in range(2):
            out = venv_sync.sync(root)
            assert out["state"] == "failed" and not out["ok"]
            assert "resolution failed" in out["detail"]
        assert calls == [(root, True), (root, True)]


class TestConfiguredPlatformExtras:
    def test_source_update_dep_sync_carries_configured_platform_extra(self, tmp_path, monkeypatch):
        """#122535: the source-update dependency sync must carry the declared extra of a
        CONFIGURED platform. The recorded ledger unions, but an install whose SDK only ever
        arrived through the shrinking ``[all]`` selection (or a hand install) recorded it
        nowhere, so the sync rebuilds without it and the channel is dead after restart."""
        import pm
        import pm.features
        from gateway.config import Platform

        calls = []
        monkeypatch.setattr(pm, "sync_venv", lambda *args, **kwargs: calls.append((args, kwargs)))
        monkeypatch.setattr(venv_sync, "refuse_foreign_owned_venv", lambda root: None)
        monkeypatch.setattr(venv_sync, "collect_superseded_generations", lambda root: None)
        # A source install: no frozen enabled-features.json beside the byte store.
        store = tmp_path / "tools"
        store.mkdir()
        monkeypatch.setattr("pm.paths.store_root", lambda: store)
        # An established PM install: runtime facts exist, so no legacy selection runs.
        facts = tmp_path / "facts.json"
        facts.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("pm.environments.runtime_facts_path", lambda root: facts)
        monkeypatch.setattr("pm.client.ensure_tools_for_sync", lambda: None)

        class _Config:
            def get_connected_platforms(self):
                return [Platform.FEISHU]

        monkeypatch.setattr("gateway.config.load_gateway_config", lambda: _Config())

        venv_sync._sync_source_dependencies(REPO_ROOT, arm=False)

        assert calls, "the source-update dependency sync never ran"
        args, kwargs = calls[0]
        extras = args[0] if args else kwargs.get("extras")
        assert "feishu" in (extras or []), (
            "a configured Feishu platform must reach the source-update dependency sync "
            f"as the 'feishu' extra, got {extras!r} -- without it the rebuilt venv has no "
            "lark-oapi and the channel fails to load after the restart"
        )

    def test_frozen_bundle_drops_configured_platform_extras_the_sync_would_refuse(self, tmp_path, monkeypatch):
        """#122535: a frozen bundle (lazy installs off) must not fail the update for a
        configured backend whose extra sits outside ``enabled-features.json`` -- the extras
        handed to the sync have to be ones the real policy accepts."""
        import pm.install
        from gateway.config import Platform

        store = tmp_path / "tools"
        store.mkdir()
        monkeypatch.setattr("pm.paths.store_root", lambda: store)
        pm.features.write_features(["all", "slack"], store.parent)
        monkeypatch.setattr(pm.install, "lazy_installs_allowed", lambda: False)

        class _Config:
            def get_connected_platforms(self):
                return [Platform.FEISHU, Platform.SLACK]

        monkeypatch.setattr("gateway.config.load_gateway_config", lambda: _Config())

        extras = venv_sync.configured_platform_extras(REPO_ROOT)

        assert "feishu" not in extras, "a frozen bundle cannot carry an extra it does not ship"
        assert "slack" in extras, "an extra the bundle does ship must still ride along"
        pm.install._feature_policy(extras, repair=False)  # the update sync accepts these

    def test_source_install_without_a_feature_file_carries_the_configured_extra(self, tmp_path, monkeypatch):
        """No frozen declaration (a source install): the configured platform's extra rides along."""
        from gateway.config import Platform

        store = tmp_path / "tools"
        store.mkdir()
        monkeypatch.setattr("pm.paths.store_root", lambda: store)

        class _Config:
            def get_connected_platforms(self):
                return [Platform.FEISHU]

        monkeypatch.setattr("gateway.config.load_gateway_config", lambda: _Config())

        assert venv_sync.configured_platform_extras(REPO_ROOT) == ["feishu"]


class TestUpdateTakeoverCarriesConfiguredPlatformExtras:
    """The takeover leg: `hermes update` hands the configured platforms to the sync too."""

    def _wire(self, tmp_path, monkeypatch, *, venv_current=False):
        from contextlib import nullcontext
        from gateway.config import Platform

        root = tmp_path / "co"
        root.mkdir()
        (root / "pyproject.toml").write_text(
            "[project]\nname='x'\n[project.optional-dependencies]\nfeishu = ['lark-oapi==1.6.8']\n",
            encoding="utf-8")
        state = tmp_path / "installs"
        state.mkdir()
        store = tmp_path / "tools"
        store.mkdir()
        # No enabled-features.json here: a source install unions extras freely.
        monkeypatch.setattr("pm.paths.store_root", lambda: store)
        monkeypatch.setattr("pm.environments.install_state_dir", lambda root: state)
        monkeypatch.setattr("pm.environments.runtime_facts_path", lambda root: state / "facts.json")
        monkeypatch.setattr("pm.environments.activation_environment", lambda root: {"ACTIVE": str(root)})
        monkeypatch.setattr("pm.extras.legacy_selection", lambda root: None)
        monkeypatch.setattr("pm.client.ensure_tools_for_sync", lambda: None)
        monkeypatch.setattr("pm.client.venv_is_current", lambda *, project_root: venv_current)
        monkeypatch.setattr("pm.receipt.worker_context", lambda correlation: nullcontext())
        monkeypatch.setattr("pm.receipt.last_for_update", lambda update_id: {"update_id": update_id})
        monkeypatch.setattr("hermes_cli.update_stage.ensure_panel", lambda root: None)
        monkeypatch.setattr("hermes_cli.update_stage.publish_stage", lambda message: None)
        monkeypatch.setattr("hermes_cli.venv_sync.publish_launchers", lambda root: None)
        monkeypatch.setattr("hermes_cli._launchers.resolve_store_python", lambda root: tmp_path / "python.exe")

        class _Config:
            def get_connected_platforms(self):
                return [Platform.FEISHU]

        monkeypatch.setattr("gateway.config.load_gateway_config", lambda: _Config())

        syncs = []
        monkeypatch.setattr("pm.client.sync_venv", lambda *args, **kwargs: syncs.append((args, kwargs)))
        return root, state, syncs

    def test_takeover_preparation_carries_configured_platform_extras(self, tmp_path, monkeypatch):
        from hermes_cli import _update_takeover

        root, _state, syncs = self._wire(tmp_path, monkeypatch)

        _update_takeover.prepare({"root": str(root), "update_id": "u" * 32})

        assert syncs, "the takeover never synced dependencies"
        assert syncs[0][0][0] == ["feishu"], syncs[0]
        assert syncs[0][1]["repair"] is False

    def test_repair_takeover_adds_no_features(self, tmp_path, monkeypatch):
        """Repair restores the recorded graph; carrying extras into it would change the graph."""
        from hermes_cli import _update_takeover

        root, state, syncs = self._wire(tmp_path, monkeypatch, venv_current=True)
        (state / ".repair-incomplete").write_text("{}", encoding="utf-8")

        _update_takeover.prepare({"root": str(root), "update_id": "u" * 32})

        assert syncs and syncs[0][0] == (None,), syncs[0]
        assert syncs[0][1]["repair"] is True


class TestSealedTrees:
    def test_a_sealed_tree_is_a_clean_noop(self, tmp_path, monkeypatch):
        """The desktop payload and nix bundle must not fail, must not sync."""
        root = tmp_path / "sealed"
        root.mkdir()
        (root / "install-stamp.json").write_text(
            json.dumps({"commit": "abc123", "payload": "full", "updateMechanism": "electron-updater"})
        )
        calls = _wire_pm(monkeypatch)

        out = venv_sync.sync(root)

        assert out == {"state": "sealed", "ok": True}
        assert calls == []

    def test_a_dev_tree_with_both_stamp_and_git_is_a_checkout(
        self, tmp_path, monkeypatch
    ):
        root = _checkout(tmp_path)
        (root / "install-stamp.json").write_text(
            json.dumps({"commit": "abc", "updateMechanism": "electron-updater"})
        )
        _wire_pm(monkeypatch)

        assert venv_sync.sync(root)["state"] == "synced"

    def test_a_stamp_without_update_mechanism_is_a_build_lane_bug(self, tmp_path):
        root = tmp_path / "sealed"
        root.mkdir()
        (root / "install-stamp.json").write_text(json.dumps({"commit": "abc"}))
        with pytest.raises(RuntimeError, match="updateMechanism"):
            venv_sync.sync(root)


class TestCliContract:
    def test_json_output_and_exit_codes(self, tmp_path):
        """post_update and the installers read exactly this."""
        root = tmp_path / "sealed"
        root.mkdir()
        (root / "install-stamp.json").write_text(
            json.dumps({"commit": "x", "updateMechanism": "electron-updater"})
        )

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "hermes_cli.venv_sync",
                "--project-root",
                str(root),
                "--json",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout) == {"state": "sealed", "ok": True}

    def test_failure_exits_nonzero(self, tmp_path):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "hermes_cli.venv_sync",
                "--project-root",
                str(tmp_path),  # empty dir: no pyproject, no stamp
                "--json",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

        assert proc.returncode == 1
        assert json.loads(proc.stdout)["state"] == "failed"
