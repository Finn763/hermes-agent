"""Issue #48987: CLI exit must reap local background sessions.

On Windows a live process whose cwd sits inside a folder holds a directory
handle (WinError 32): the folder cannot be deleted afterwards, even as
Administrator. Hermes spawned background terminal sessions with
cwd=session dir but ``_run_cleanup`` never killed them, so an orphaned
child kept the user's folder locked after Hermes exited.
"""
import sys
import shutil
import time

import tools.process_registry as pr_mod
from tools.process_registry import ProcessRegistry


def _sleep_cmd(seconds=60):
    exe = sys.executable.replace("\\", "/")
    return f'"{exe}" -c "import time; time.sleep({seconds})"'


def _wait_dead(reg, sid, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if reg.poll(sid)["status"] == "exited":
            return True
        time.sleep(0.05)
    return False


def test_shutdown_reap_terminates_session_in_chinese_dir(tmp_path):
    work = tmp_path / "中文文件夹"
    work.mkdir()
    reg = ProcessRegistry()
    session = reg.spawn_local(_sleep_cmd(), cwd=str(work))
    try:
        assert reg.count_running() >= 1
        killed = pr_mod.reap_orphaned_background_sessions(registry=reg)
        assert killed >= 1
        assert reg.count_running() == 0
        assert _wait_dead(reg, session.id), "child still alive after reap"
        # Issue-level proof: the folder is deletable once sessions are reaped.
        shutil.rmtree(work)
        assert not work.exists()
        # Second call is a safe no-op.
        assert pr_mod.reap_orphaned_background_sessions(registry=reg) == 0
    finally:
        reg.kill_all(source="test_cleanup")
