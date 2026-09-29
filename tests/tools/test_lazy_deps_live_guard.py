"""RED tests for #119364: _venv_pip_install must refuse live-venv installs.

Fails on main (no guard -> subprocess invoked), passes with the guard.
"""
from __future__ import annotations

import subprocess
import sys
import types

import tools.lazy_deps as ld


def _block_subprocess(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("subprocess must not run for refused live-venv install")
    monkeypatch.setattr(subprocess, "run", _boom)


def test_loaded_module_refuses_without_subprocess(monkeypatch):
    mod = types.ModuleType("zzzfakelivepkg")
    monkeypatch.setitem(sys.modules, "zzzfakelivepkg", mod)
    _block_subprocess(monkeypatch)
    r = ld._venv_pip_install(("zzzfakelivepkg>=1",))
    assert r.success is False
    assert "already imported" in r.stderr


def test_symlink_dist_info_refuses(monkeypatch, tmp_path):
    real = tmp_path / "zzzfakesym-1.0.dist-info"
    real.mkdir()
    link = tmp_path / "zzzfakesym-2.0.dist-info"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        import pytest
        pytest.skip("no symlink privilege")
    monkeypatch.setattr("sysconfig.get_paths", lambda: {"purelib": str(tmp_path)})
    _block_subprocess(monkeypatch)
    r = ld._venv_pip_install(("zzzfakesym>=1",))
    assert r.success is False
    assert "symlink" in r.stderr


def test_duplicate_dist_info_refuses(monkeypatch, tmp_path):
    (tmp_path / "zzzfakedup-1.0.dist-info").mkdir()
    (tmp_path / "zzzfakedup-2.0.dist-info").mkdir()
    monkeypatch.setattr("sysconfig.get_paths", lambda: {"purelib": str(tmp_path)})
    _block_subprocess(monkeypatch)
    r = ld._venv_pip_install(("zzzfakedup>=1",))
    assert r.success is False
    assert "uv sync" in r.stderr


def test_durable_target_bypasses_guard(monkeypatch, tmp_path):
    mod = types.ModuleType("zzzfakedurpkg")
    monkeypatch.setitem(sys.modules, "zzzfakedurpkg", mod)
    monkeypatch.setenv("HERMES_LAZY_INSTALL_TARGET", str(tmp_path / "tgt"))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    r = ld._venv_pip_install(("zzzfakedurpkg>=1",))
    assert r.success is True
