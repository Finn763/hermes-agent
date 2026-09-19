"""``project_venv_dir`` must resolve the venv of an install whose interpreter lives OUTSIDE the checkout.

``$HERMES_HOME/venvs/<name>`` is the layout the shipped Windows launchers assume, and such a checkout
holds neither ``venv`` nor ``.venv``. Every update-path caller shares the
``project_venv_dir(root) or root / "venv"`` idiom, so a ``None`` made them invent ``<checkout>/venv``,
hand ``uv`` a ``VIRTUAL_ENV`` that does not exist, skip the import probe and reclassify every
``hermes tools`` dependency as missing (#116148). The running interpreter's venv is the truthful answer;
a root that is not the checkout this module was loaded from must keep resolving to ``None``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import hermes_constants
from hermes_constants import project_venv_dir, venv_python_path


@pytest.fixture
def checkout(tmp_path):
    """A checkout with no in-tree venv — the reported (out-of-tree) layout."""
    root = tmp_path / "hermes-agent"
    root.mkdir()
    return root


@pytest.fixture
def running_venv(tmp_path):
    """:data:`sys.prefix` stand-in: an install-managed venv holding a real interpreter file."""
    venv = tmp_path / "venvs" / "hermes"
    interpreter = venv_python_path(venv)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    return venv


def _running_from(monkeypatch, checkout, venv):
    """Make *checkout* the checkout this module was loaded from, running under *venv*."""
    monkeypatch.setattr(hermes_constants, "__file__", str(checkout / "hermes_constants.py"))
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", str(checkout / "no-such-base"))


def test_out_of_tree_install_resolves_the_running_interpreter_venv(
    monkeypatch, checkout, running_venv
):
    """The bug: no in-tree venv meant ``None``, i.e. a ``VIRTUAL_ENV`` that does not exist."""
    _running_from(monkeypatch, checkout, running_venv)

    assert project_venv_dir(checkout) == running_venv
    assert venv_python_path(project_venv_dir(checkout)).is_file()


def test_in_tree_venv_still_wins(monkeypatch, checkout, running_venv):
    """A dev checkout keeps resolving its own ``venv``/``.venv``."""
    (checkout / ".venv").mkdir()
    _running_from(monkeypatch, checkout, running_venv)

    assert project_venv_dir(checkout) == checkout / ".venv"


def test_foreign_root_keeps_none(monkeypatch, checkout, running_venv, tmp_path):
    """A temp dir / another clone must not claim the running interpreter's venv."""
    other = tmp_path / "not-our-checkout"
    other.mkdir()
    _running_from(monkeypatch, checkout, running_venv)

    assert project_venv_dir(other) is None


def test_out_of_tree_venv_without_interpreter_keeps_none(monkeypatch, checkout, tmp_path):
    """A half-deleted venv reads as absent, so callers keep failing safe rather than at a dead path."""
    empty = tmp_path / "venvs" / "half-deleted"
    empty.mkdir(parents=True)
    _running_from(monkeypatch, checkout, empty)

    assert project_venv_dir(checkout) is None


def test_base_interpreter_keeps_none(monkeypatch, checkout, running_venv):
    """No active venv: never point the callers' writes at a base interpreter."""
    _running_from(monkeypatch, checkout, running_venv)
    monkeypatch.setattr(sys, "base_prefix", str(running_venv))

    assert project_venv_dir(checkout) is None
