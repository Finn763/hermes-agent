"""Tests: bot_relay.* JSON-RPC handlers (tui_gateway/methods_bot_relay.py).

The Desktop's relay door on each connected gateway. Contracts:
- roster.sync persists validated rows and reports the accepted count;
- outbox.drain returns queued envelopes exactly once;
- deliver validates the target profile against THIS install and runs the
  one-turn Bot Chat transport (subprocess is faked here — the argv contract
  is what's pinned);
- reply writes the waiter's file and rejects malformed envelope ids.
"""

from __future__ import annotations

import json

import pytest

import tui_gateway.server as srv
from tools import bot_relay


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".hermes"
    (h / "profiles" / "ops").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(h))
    return h


def _result(envelope):
    assert "error" not in envelope, envelope
    return envelope["result"]


def test_roster_sync_persists_and_counts(home):
    out = _result(
        srv._methods["bot_relay.roster.sync"](
            1,
            {
                "agents": [
                    {"profile": "scout", "handle": "scout", "connection_id": "cloud-1"},
                    {"profile": "", "connection_id": "cloud-1"},  # dropped
                ]
            },
        )
    )
    assert out["count"] == 1
    assert [r["profile"] for r in bot_relay.read_remote_roster(home)] == ["scout"]


def test_outbox_drain_returns_each_envelope_once(home):
    target = {"profile": "scout", "handle": "scout", "connection_id": "cloud-1",
              "connection_label": "", "title": "", "description": ""}
    env = bot_relay.enqueue_envelope(
        home, target=target, message="m", sender_profile="default", sender_handle="hermes"
    )
    first = _result(srv._methods["bot_relay.outbox.drain"](1, {}))
    assert [e["id"] for e in first["envelopes"]] == [env["id"]]
    second = _result(srv._methods["bot_relay.outbox.drain"](2, {}))
    assert second["envelopes"] == []


def test_deliver_validates_profile_and_runs_transport(home, monkeypatch):
    calls = {}

    class _Proc:
        returncode = 0
        stdout = "pong from ops"
        stderr = ""

    def _fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr("subprocess.run", _fake_run)
    out = _result(
        srv._methods["bot_relay.deliver"](1, {"profile": "ops", "message": "ping"})
    )
    assert out["reply"] == "pong from ops"
    # Decoding is pinned (#93590 sibling defect): without encoding= the
    # child's UTF-8 output is decoded with the locale codec — cp1252/GBK on
    # Windows — mangling non-ASCII replies; errors="replace" keeps a bad
    # byte from raising instead of delivering.
    assert calls["kwargs"]["encoding"] == "utf-8"
    assert calls["kwargs"]["errors"] == "replace"
    argv = calls["argv"]
    # argv[0] may be a resolved venv path (#93590) — match by basename.
    assert argv[1:3] == ["-p", "ops"]
    assert argv[0].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] in ("hermes", "hermes.exe")
    assert "Bot Chat" in argv and "--query-file" in argv

    # 'hermes' alias resolves to default
    _result(srv._methods["bot_relay.deliver"](2, {"profile": "hermes", "message": "x"}))
    assert calls["argv"][1:3] == ["-p", "default"]

    # unknown profile refuses without spawning
    calls.clear()
    err = srv._methods["bot_relay.deliver"](3, {"profile": "ghost", "message": "x"})
    assert "error" in err and "ghost" in err["error"]["message"]
    assert not calls


def test_deliver_requires_params(home):
    err = srv._methods["bot_relay.deliver"](1, {"profile": "", "message": ""})
    assert "error" in err


def test_deliver_lands_in_live_bot_chat_instead_of_subprocess(home, monkeypatch):
    """#100523: a Desktop-owned Bot Chat receives the DM as a normal user turn.

    With the target's Bot Chat live in this gateway, the subprocess transport
    would be fenced out by the single-owner lease and drop the payload. The
    handler must route through prompt.submit (the composer's choke point) and
    never spawn the CLI.
    """
    spawned = []
    submitted = []

    class _Proc:
        returncode, stdout, stderr = 0, "pong", ""

    def _fake_run(argv, *a, **k):
        # The server module's import-time update prefetch runs `git ...` on a
        # daemon thread; only the relay's `hermes` CLI spawn is under test.
        if argv and argv[0] != "git":
            spawned.append(argv)
        return _Proc()

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setitem(
        srv._methods, "prompt.submit", lambda rid, p: submitted.append(p) or srv._ok(rid, {"status": "streaming"})
    )
    monkeypatch.setattr(srv, "_profile_home", lambda name: home / "profiles" / name)
    monkeypatch.setitem(
        srv._sessions,
        "live-ops",
        {"profile_home": str(home / "profiles" / "ops"), "pending_title": "Bot Chat", "history": []},
    )
    out = _result(srv._methods["bot_relay.deliver"](1, {"profile": "ops", "message": "ping"}))
    # queued=True is the invariant: a DM never interrupts a turn in flight.
    assert submitted == [{"session_id": "live-ops", "text": "ping", "queued": True}]
    assert not spawned
    assert "reply" in out

    # A live session titled anything else for the same profile does not qualify:
    # the subprocess path runs exactly as before.
    srv._sessions["live-ops"]["pending_title"] = "Scratch"
    submitted.clear()

    out = _result(srv._methods["bot_relay.deliver"](2, {"profile": "ops", "message": "ping"}))
    assert out["reply"] == "pong" and spawned and not submitted


def test_deliver_scopes_the_api_key_read_to_the_target_profile(home, monkeypatch):
    """Review on #95741: the API_SERVER_KEY read must come from the TARGET profile's
    secret scope, never this process's os.environ (which holds the launch profile's
    value — or a sibling's in a multiplexed process)."""
    (home / "profiles" / "ops" / ".env").write_text(
        "API_SERVER_KEY=ops-profile-key\n", encoding="utf-8"
    )
    monkeypatch.setenv("API_SERVER_KEY", "launch-profile-key")
    monkeypatch.setattr(srv, "_profile_home", lambda name: home / "profiles" / name)

    import agent.secret_scope as secret_scope
    from gateway.platforms._shared import get_scoped_secret

    seen = {}

    def _fake_deliver(profile, message, *, timeout=None):
        seen["scope"] = secret_scope.current_secret_scope()
        seen["key"] = get_scoped_secret("API_SERVER_KEY", "")
        return "pong"

    monkeypatch.setattr(bot_relay, "deliver_via_gateway_api", _fake_deliver)
    out = _result(
        srv._methods["bot_relay.deliver"](1, {"profile": "ops", "message": "ping"})
    )

    assert out["reply"] == "pong"
    assert seen["scope"] is not None
    assert seen["key"] == "ops-profile-key"
    # The scope is scoped to the delivery, not left installed on the RPC thread.
    assert secret_scope.current_secret_scope() is None


def test_deliver_fails_closed_when_the_profile_scope_cannot_build(home, monkeypatch):
    """A failed scope build still installs an EMPTY scope — the read must not fall
    back to os.environ (that is the cross-profile leak the scope exists to stop)."""
    monkeypatch.setattr(srv, "_profile_home", lambda name: home / "profiles" / name)

    import agent.secret_scope as secret_scope

    def _boom(_home):
        raise OSError("unreadable profile secrets")

    monkeypatch.setattr(secret_scope, "build_profile_secret_scope", _boom)
    seen = {}

    def _fake_deliver(profile, message, *, timeout=None):
        seen["scope"] = secret_scope.current_secret_scope()
        return "pong"

    monkeypatch.setattr(bot_relay, "deliver_via_gateway_api", _fake_deliver)
    _result(srv._methods["bot_relay.deliver"](1, {"profile": "ops", "message": "ping"}))

    assert seen["scope"] == {}


def test_deliver_does_not_fall_back_when_the_api_outcome_is_unknown(home, monkeypatch):
    """A dropped turn response must surface, not re-run the message through the CLI
    transport (that would deliver it twice)."""
    spawned = []

    def _fake_run(argv, *a, **k):
        spawned.append(argv)
        class _Proc:
            returncode, stdout, stderr = 0, "pong", ""
        return _Proc()

    monkeypatch.setattr("subprocess.run", _fake_run)

    def _uncertain(profile, message, *, timeout=None):
        raise bot_relay.GatewayApiDeliveryUncertain("simulated dropped response")

    monkeypatch.setattr(bot_relay, "deliver_via_gateway_api", _uncertain)
    err = srv._methods["bot_relay.deliver"](1, {"profile": "ops", "message": "ping"})

    assert "error" in err and "duplicate" in err["error"]["message"]
    assert not spawned


def test_reply_roundtrip_and_id_validation(home):
    envelope_id = "c" * 32
    _result(srv._methods["bot_relay.reply"](1, {"id": envelope_id, "reply": "hi"}))
    path = bot_relay.relay_root(home) / bot_relay.REPLIES_DIR / f"{envelope_id}.json"
    assert json.loads(path.read_text(encoding="utf-8"))["reply"] == "hi"

    err = srv._methods["bot_relay.reply"](2, {"id": "../evil"})
    assert "error" in err


def test_deliver_write_failure_still_removes_tempfile(home, monkeypatch, tmp_path):
    """A failed payload write must not leak the relay DM tempfile."""
    import glob
    import os
    import tempfile as _tempfile

    made = []
    real_mkstemp = _tempfile.mkstemp

    def _tracking_mkstemp(*args, **kwargs):
        kwargs["dir"] = str(tmp_path)
        fd, path = real_mkstemp(*args, **kwargs)
        made.append(path)
        return fd, path

    class _BrokenWriter:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def write(self, content):
            raise OSError("disk full")

    monkeypatch.setattr("tempfile.mkstemp", _tracking_mkstemp)
    monkeypatch.setattr("os.fdopen", lambda *a, **k: _BrokenWriter())
    err = srv._methods["bot_relay.deliver"](1, {"profile": "ops", "message": "x"})
    assert "error" in err
    assert made, "mkstemp was never reached"
    assert not glob.glob(str(tmp_path / "hermes-relay-dm-*")), "tempfile leaked"
