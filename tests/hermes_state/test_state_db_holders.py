"""Behavioral tests for the state-holder and repair-admission authority."""

import os
import sys

import pytest

import hermes_state_holders


@pytest.mark.linux_only
def test_foreign_holder_accepts_same_inode_reached_through_an_alias(
    tmp_path, monkeypatch
):
    """Descriptor identity is authoritative even when /proc spells another path."""
    db_path = tmp_path / "state.db"
    db_path.touch()
    alias_path = tmp_path / "namespace-alias" / "state.db"

    proc_root = tmp_path / "proc"
    for pid in (111, 222):
        (proc_root / str(pid) / "fd").mkdir(parents=True)
    os.symlink(db_path, proc_root / "222" / "fd" / "3")

    monkeypatch.setattr(hermes_state_holders.os, "getpid", lambda: 111)
    real_listdir = os.listdir

    def _listdir(path):
        if isinstance(path, str):
            path = path.replace("/proc", str(proc_root))
        return real_listdir(path)

    monkeypatch.setattr(hermes_state_holders.os, "listdir", _listdir)

    def _readlink(path):
        if path == "/proc/222/fd/3":
            return str(alias_path)
        return os.readlink(path.replace("/proc", str(proc_root)))

    monkeypatch.setattr(hermes_state_holders.os, "readlink", _readlink)
    real_stat = os.stat

    def _stat(path, *args, **kwargs):
        path = str(path).replace("/proc", str(proc_root))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(hermes_state_holders.os, "stat", _stat)

    assert hermes_state_holders.foreign_state_db_holders(db_path) == [
        (222, str(alias_path))
    ]


class _FakeOpened:
    def __init__(self, path):
        self.path = path


class _FakeProcess:
    def __init__(self, pid, open_files):
        self.info = {"pid": pid}
        self._open_files = [_FakeOpened(path) for path in open_files]

    def open_files(self):
        return self._open_files


class _UninspectableProcess:
    info = {"pid": 9999}

    def open_files(self):
        raise PermissionError("denied")


class _FakePsutil:
    def __init__(self, processes):
        self._processes = processes

    def process_iter(self, attrs=None):
        return iter(self._processes)


def _force_windows_scan(monkeypatch):
    monkeypatch.setattr(hermes_state_holders, "_IS_WINDOWS", True)
    monkeypatch.setattr(sys, "platform", "win32")


def test_windows_holder_found_via_psutil_scan(tmp_path, monkeypatch):
    """Windows must enumerate holders, not return [] (#120205)."""
    db_path = tmp_path / "state.db"
    db_path.touch()
    db_real = os.path.realpath(db_path)
    foreign_pid = 4242
    monkeypatch.setattr(hermes_state_holders.os, "getpid", lambda: 111)
    monkeypatch.setattr(
        hermes_state_holders,
        "psutil",
        _FakePsutil([_FakeProcess(foreign_pid, [db_real])]),
    )
    _force_windows_scan(monkeypatch)

    assert hermes_state_holders.foreign_state_db_holders(db_path) == [
        (foreign_pid, db_real)
    ]


def test_windows_own_process_is_not_a_holder(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db_path.touch()
    db_real = os.path.realpath(db_path)
    monkeypatch.setattr(hermes_state_holders.os, "getpid", lambda: 111)
    monkeypatch.setattr(
        hermes_state_holders,
        "psutil",
        _FakePsutil([_FakeProcess(111, [db_real])]),
    )
    _force_windows_scan(monkeypatch)

    assert hermes_state_holders.foreign_state_db_holders(db_path) == []


def test_windows_scan_unavailable_fails_closed(tmp_path, monkeypatch):
    """psutil=None on Windows must refuse, never read as all-clear (#120205)."""
    db_path = tmp_path / "state.db"
    db_path.touch()
    monkeypatch.setattr(hermes_state_holders, "psutil", None)
    _force_windows_scan(monkeypatch)

    assert hermes_state_holders.foreign_state_db_holders(db_path) == [
        (-1, "open-file scan unavailable")
    ]
    assert (
        hermes_state_holders.held_store_refusal(db_path, command="optimize-storage")
        is not None
    )


def test_windows_uninspectable_process_does_not_fail_scan(
    tmp_path, monkeypatch
):
    """One denied process is skipped; a visible holder is still found."""
    db_path = tmp_path / "state.db"
    db_path.touch()
    db_real = os.path.realpath(db_path)
    foreign_pid = 4242
    monkeypatch.setattr(hermes_state_holders.os, "getpid", lambda: 111)
    monkeypatch.setattr(
        hermes_state_holders,
        "psutil",
        _FakePsutil(
            [_UninspectableProcess(), _FakeProcess(foreign_pid, [db_real])]
        ),
    )
    _force_windows_scan(monkeypatch)

    assert hermes_state_holders.foreign_state_db_holders(db_path) == [
        (foreign_pid, db_real)
    ]
