"""Signal-supervised restarts owe the next boot a home-channel online notice (#121937).

A systemd ``restart`` (SIGTERM) sends the shutdown goodbye but writes neither
``.restart_notify.json`` nor ``.restart_pending.json``, so the revived process
stays silent: goodbye without hello. The shutdown path already assumes a
signal stop is revived by the service manager (exit 1 + ``gateway_state=running``),
so the persist phase must leave the planned-restart marker behind.
"""

import time

import pytest

import gateway.run as gateway_run
from gateway.run import GatewayRunner
from gateway.run_shutdown import GatewayShutdownMixin
from tests.gateway.restart_test_helpers import make_restart_runner


def _persist_phase_runner(**flags):
    runner, _adapter = make_restart_runner()
    for key, value in flags.items():
        setattr(runner, key, value)
    runner._stop_persist_exit_state = GatewayRunner._stop_persist_exit_state.__get__(
        runner, GatewayRunner
    )
    return runner


def _finished_ctx():
    ctx = GatewayShutdownMixin._StopContext(deferred_count=lambda: 0)
    ctx.started_at = time.monotonic()
    return ctx


@pytest.mark.asyncio
async def test_signal_shutdown_leaves_planned_restart_marker(tmp_path, monkeypatch):
    """SIGTERM shutdown (systemd restart) must write ``.restart_pending.json``."""
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    runner = _persist_phase_runner(
        _signal_initiated_shutdown=True,
        _restart_requested=False,
        _restart_command_source=None,
    )
    await runner._stop_persist_exit_state(_finished_ctx())

    assert (tmp_path / ".restart_pending.json").exists()


@pytest.mark.asyncio
async def test_plain_stop_leaves_no_restart_marker(tmp_path, monkeypatch):
    """A programmatic stop (no signal, no restart) stays silent on next boot."""
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    runner = _persist_phase_runner(
        _signal_initiated_shutdown=False,
        _restart_requested=False,
        _restart_command_source=None,
    )
    await runner._stop_persist_exit_state(_finished_ctx())

    assert not (tmp_path / ".restart_pending.json").exists()
