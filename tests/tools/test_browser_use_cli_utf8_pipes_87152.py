"""#87152 regression: browser-use CLI subprocess must pin UTF-8 on both pipes.

`subprocess.run(..., text=True)` without `encoding=` decodes with
`locale.getpreferredencoding()` — cp1252 on US Windows, cp936 (GBK) on
CJK Windows. The browser-use CLI speaks UTF-8 in both directions, so any
non-ASCII byte crashes the reader thread (`UnicodeDecodeError` in
`_readerthread`, empty tool output, #87152) and GBK-encoded stdin code
crashes the CLI itself. `install_cli` in the same file already pins the
pair (`encoding="utf-8", errors="replace"`); `browser_exec` did not.

Contract under test: `browser_exec` calls `subprocess.run` with
`encoding="utf-8"` and `errors="replace"`, exactly like `install_cli`.

ponytail: one contract assertion. The CI env is typically UTF-8, so a
naive end-to-end test would skip; the contract catches the regression
regardless of host locale. Add a Windows-only bytes-roundtrip test if
you want evidence the pin is load-bearing on cp1252/cp936 hosts.
"""
from unittest.mock import patch

import pytest

import tools.browser_use_cli as browser_use_cli


def _run_browser_exec_capturing_subprocess(monkeypatch):
    """Invoke `browser_exec` with everything that would touch the real CLI
    neutralized, and return the kwargs passed to `subprocess.run`."""
    captured = {}

    class _FakeProc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(browser_use_cli.subprocess, "run", fake_run)
    # Short-circuit every side path that would otherwise try the real CLI,
    # reach the network, or touch the filesystem.
    monkeypatch.setattr(browser_use_cli, "_find_cli", lambda: ["browser-use", "exec"])
    monkeypatch.setattr(browser_use_cli, "_base_subprocess_env", lambda: {})
    monkeypatch.setattr(browser_use_cli, "_read_browser_cfg", lambda: {})
    monkeypatch.setattr(
        browser_use_cli, "is_legacy_browser_use_cloud_config", lambda cfg: False
    )
    monkeypatch.setattr(browser_use_cli, "_workspace_dir", lambda task_id: None)
    # _resolve_backend_cdp is the modern name; fall back to the legacy alias
    # if the local checkout still uses it.
    if hasattr(browser_use_cli, "_resolve_backend_cdp"):
        def _no_backend(env, task_id=None, session_name=""):
            return None
        monkeypatch.setattr(browser_use_cli, "_resolve_backend_cdp", _no_backend)

    browser_use_cli.browser_exec("print('hello')")

    assert captured, "browser_exec never reached subprocess.run"
    return captured["kwargs"]


def test_browser_exec_pins_utf8_pipes(monkeypatch):
    """`browser_exec` must pass `encoding='utf-8'` (and `errors='replace'`)
    to `subprocess.run`, matching `install_cli`. Without it, text=True
    falls back to `locale.getpreferredencoding()` and crashes the reader
    thread on any non-ASCII byte (#87152)."""
    kwargs = _run_browser_exec_capturing_subprocess(monkeypatch)

    assert kwargs.get("encoding") == "utf-8", (
        "browser_exec must pin encoding='utf-8' on the CLI pipes: text=True "
        "without encoding decodes with the Windows ANSI code page and crashes "
        "on non-ASCII bytes (#87152)."
    )
    assert kwargs.get("errors") == "replace", (
        "browser_exec must pin errors='replace' to match install_cli and "
        "survive malformed UTF-8 bytes in CLI output."
    )
    # text=True stays on (it's how the existing call is structured); the
    # regression is the missing encoding/errors, not the text mode.
    assert kwargs.get("text") is True