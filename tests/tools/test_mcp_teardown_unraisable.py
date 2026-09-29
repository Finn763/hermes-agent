"""sys.unraisablehook filter for benign asyncio teardown noise (#81175).

``BaseSubprocessTransport.__del__`` (unix) and
``_ProactorBasePipeTransport.__del__`` (Windows) can fire after the event
loop closes. That path bypasses the loop exception handler entirely and
reaches ``sys.unraisablehook``, printing "Exception ignored in: ..." noise.
``tools.mcp_tool`` installs a filter that swallows ONLY that benign pattern.
"""
import asyncio
import gc
import sys
from types import SimpleNamespace

import pytest


def _unraisable(exc, err_msg):
    return SimpleNamespace(
        exc_type=type(exc),
        exc_value=exc,
        exc_traceback=getattr(exc, "__traceback__", None),
        err_msg=err_msg,
        object=None,
    )


def _unix_del_msg():
    return (
        "Exception ignored in: "
        "<function BaseSubprocessTransport.__del__ at 0x7f1234567890>"
    )


def _win_del_msg():
    return (
        "Exception ignored in: "
        "<function _ProactorBasePipeTransport.__del__ at 0x7f1234567890>"
    )


class TestBenignTeardownDetector:
    def test_unix_subprocess_del_is_benign(self):
        from tools.mcp_tool import _is_benign_asyncio_teardown

        u = _unraisable(RuntimeError("Event loop is closed"), _unix_del_msg())
        assert _is_benign_asyncio_teardown(u) is True

    def test_windows_proactor_del_is_benign(self):
        from tools.mcp_tool import _is_benign_asyncio_teardown

        u = _unraisable(
            ValueError("I/O operation on closed pipe"), _win_del_msg()
        )
        assert _is_benign_asyncio_teardown(u) is True

    def test_asyncio_frame_covers_del_free_path(self):
        """A real closed-loop traceback from asyncio/base_events.py matches
        even when err_msg carries no __del__ marker."""
        from tools.mcp_tool import _is_benign_asyncio_teardown

        loop = asyncio.new_event_loop()
        loop.close()
        try:
            loop.call_soon(lambda: None)
        except RuntimeError as exc:
            assert "asyncio" in exc.__traceback__.tb_next.tb_frame.f_code.co_filename
            u = _unraisable(exc, "Exception ignored in: garbage collection")
            assert _is_benign_asyncio_teardown(u) is True
        else:
            pytest.fail("closed loop did not raise")

    def test_none_exc_value_is_not_benign(self):
        from tools.mcp_tool import _is_benign_asyncio_teardown

        u = SimpleNamespace(
            exc_type=None, exc_value=None, exc_traceback=None,
            err_msg=_unix_del_msg(), object=None,
        )
        assert _is_benign_asyncio_teardown(u) is False

    def test_wrong_type_is_not_benign(self):
        from tools.mcp_tool import _is_benign_asyncio_teardown

        u = _unraisable(TypeError("Event loop is closed"), _unix_del_msg())
        assert _is_benign_asyncio_teardown(u) is False

    def test_message_alone_without_teardown_context_is_not_benign(self):
        """Same message from plain (non-asyncio, non-__del__) code stays loud."""
        from tools.mcp_tool import _is_benign_asyncio_teardown

        try:
            raise RuntimeError("Event loop is closed")
        except RuntimeError as exc:
            u = _unraisable(exc, "Exception ignored in: something else")
            assert _is_benign_asyncio_teardown(u) is False

    def test_del_with_unrelated_error_stays_loud(self):
        from tools.mcp_tool import _is_benign_asyncio_teardown

        u = _unraisable(RuntimeError("genuine boom"), _unix_del_msg())
        assert _is_benign_asyncio_teardown(u) is False


class TestHookInstall:
    def _reinstall_with_fake_previous(self, monkeypatch):
        import tools.mcp_tool as mcp_mod

        forwarded = []
        monkeypatch.setattr(
            sys, "_hermes_mcp_teardown_hook_installed", False, raising=False
        )
        monkeypatch.setattr(sys, "unraisablehook", forwarded.append)
        mcp_mod._install_asyncio_teardown_quiet_hook()
        return forwarded

    def test_install_is_idempotent(self, monkeypatch):
        import tools.mcp_tool as mcp_mod

        self._reinstall_with_fake_previous(monkeypatch)
        hook_before = sys.unraisablehook
        mcp_mod._install_asyncio_teardown_quiet_hook()
        assert sys.unraisablehook is hook_before

    def test_hook_swallows_benign_and_forwards_rest(self, monkeypatch):
        forwarded = self._reinstall_with_fake_previous(monkeypatch)

        sys.unraisablehook(
            _unraisable(RuntimeError("Event loop is closed"), _unix_del_msg())
        )
        sys.unraisablehook(
            _unraisable(
                ValueError("I/O operation on closed pipe"), _win_del_msg()
            )
        )
        assert forwarded == []

        other = _unraisable(ValueError("real bug"), _unix_del_msg())
        sys.unraisablehook(other)
        assert forwarded == [other]

    def test_real_del_pipeline(self, monkeypatch):
        """End to end through CPython's own __del__ -> sys.unraisablehook
        path (real UnraisableHookArgs, real err_msg from the interpreter)."""
        forwarded = self._reinstall_with_fake_previous(monkeypatch)

        class _BenignRaiser:
            def __del__(self):
                raise RuntimeError("Event loop is closed")

        class _LoudRaiser:
            def __del__(self):
                raise RuntimeError("genuine boom")

        _BenignRaiser()
        gc.collect()
        assert forwarded == []

        _LoudRaiser()
        gc.collect()
        assert len(forwarded) == 1
        assert isinstance(forwarded[0].exc_value, RuntimeError)
