"""RED for #125762: N refs must resolve in one `op inject`, not N `op read`."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.secret_sources import onepassword as op  # noqa: E402


def _ok(value: str):
    return mock.Mock(returncode=0, stdout=value, stderr="")


def test_fetch_uses_single_inject_for_many_refs(monkeypatch, tmp_path):
    op._reset_cache_for_tests(tmp_path)
    fake_op = tmp_path / "op"
    fake_op.write_text("")
    values = {
        "op://V/Item A/F1": "v1",
        "op://V/Item B/F2": "v2",
        "op://V/Item C/F3": "v3",
    }
    calls = {"n": 0}
    seen_templates: list[str] = []

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        assert "op://" not in " ".join(cmd[2:3]), cmd  # ref must not be argv
        if "inject" in cmd:
            template = kwargs.get("input", "")
            seen_templates.append(template)
            # emulate op inject: replace each {{ ref }} with its value
            out = template
            for ref, val in values.items():
                out = out.replace("{{ " + ref + " }}", val)
            return _ok(out)
        ref = cmd[cmd.index("--") + 1]
        return _ok(values[ref])

    monkeypatch.setattr(op.subprocess, "run", fake_run)
    secrets, warnings = op.fetch_onepassword_secrets(
        references={"K1": "op://V/Item A/F1", "K2": "op://V/Item B/F2", "K3": "op://V/Item C/F3"},
        binary=fake_op,
        use_cache=False,
    )
    assert secrets == {"K1": "v1", "K2": "v2", "K3": "v3"}, warnings
    assert calls["n"] == 1, f"expected 1 op call, got {calls['n']}"
    # spaces must go into the template unquoted
    assert "{{ op://V/Item A/F1 }}" in seen_templates[0]
    assert '"{{ op://V/Item A/F1 }}"' not in seen_templates[0]


def test_inject_fallback_resolves_only_unresolved(monkeypatch, tmp_path):
    op._reset_cache_for_tests(tmp_path)
    fake_op = tmp_path / "op"
    fake_op.write_text("")
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        if "inject" in cmd:
            template = kwargs.get("input", "")
            # resolve K1 only; leave K2's placeholder untouched
            out = template.replace("{{ op://V/I/F1 }}", "v1")
            return _ok(out)
        ref = cmd[cmd.index("--") + 1]
        assert ref == "op://V/I/F2"
        return _ok("v2")

    monkeypatch.setattr(op.subprocess, "run", fake_run)
    secrets, warnings = op.fetch_onepassword_secrets(
        references={"K1": "op://V/I/F1", "K2": "op://V/I/F2"},
        binary=fake_op,
        use_cache=False,
    )
    assert secrets == {"K1": "v1", "K2": "v2"}, warnings
    assert calls["n"] == 2, f"expected inject+1 fallback, got {calls['n']}"


def test_inject_total_failure_falls_back_to_reads(monkeypatch, tmp_path):
    op._reset_cache_for_tests(tmp_path)
    fake_op = tmp_path / "op"
    fake_op.write_text("")
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        if "inject" in cmd:
            return mock.Mock(returncode=1, stdout="", stderr="boom")
        return _ok("v")

    monkeypatch.setattr(op.subprocess, "run", fake_run)
    secrets, warnings = op.fetch_onepassword_secrets(
        references={"K1": "op://V/I/F1", "K2": "op://V/I/F2"},
        binary=fake_op,
        use_cache=False,
    )
    assert secrets == {"K1": "v", "K2": "v"}
    assert calls["n"] == 3, f"expected inject+2 reads, got {calls['n']}"
