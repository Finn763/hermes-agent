"""RED for #90366: file toolset missing on Desktop non-default profile.

A multiplexed gateway serves many profiles from one process. Process env
(TERMINAL_*) is pinned by the launch profile; a session whose own profile
selects the local backend must still see read_file/write_file/patch/
search_files. `process` (no check_fn) survives either way, which is why the
file toolset looked like the lone casualty.
"""
import importlib

terminal_tool_module = importlib.import_module("tools.terminal_tool")


def _clear_caches():
    from tools.registry import invalidate_check_fn_cache
    from model_tools import _clear_tool_defs_cache
    invalidate_check_fn_cache()
    _clear_tool_defs_cache()


def test_file_check_follows_session_profile_not_process_env(monkeypatch, tmp_path):
    from agent.secret_scope import set_multiplex_active
    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    _clear_caches()
    try:
        blaze = tmp_path / "blaze"
        blaze.mkdir()
        (blaze / "config.yaml").write_text(
            "terminal:\n  backend: local\n", encoding="utf-8"
        )
        # Launch profile pins a non-local backend with no creds.
        monkeypatch.setenv("TERMINAL_ENV", "ssh")
        monkeypatch.delenv("TERMINAL_SSH_HOST", raising=False)
        monkeypatch.delenv("TERMINAL_SSH_USER", raising=False)
        assert terminal_tool_module.check_terminal_requirements() is False
        _clear_caches()

        set_multiplex_active(True)
        token = set_hermes_home_override(str(blaze))
        try:
            from tools import check_file_requirements
            assert check_file_requirements() is True
            from model_tools import get_tool_definitions
            names = {
                t["function"]["name"]
                for t in get_tool_definitions(
                    enabled_toolsets=["file", "terminal"], quiet_mode=True
                )
            }
            assert {"read_file", "write_file", "patch", "search_files"} <= names
            assert "process" in names
        finally:
            reset_hermes_home_override(token)
            set_multiplex_active(False)
    finally:
        _clear_caches()
