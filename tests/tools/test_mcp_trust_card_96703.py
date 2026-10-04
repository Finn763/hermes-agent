"""Tests for #96703: the trust-gate approval card shows the (capped, redacted) call
arguments, and MCP elicitation offers one-shot buttons only.

Three boundaries under test:
- the gateway payload carries ``allow_session``/``allow_permanent`` False, so the
  push path renders Once/Deny only;
- the CLI prompt is invoked with ``allow_session=False`` for the same reason;
- the replay path (``approval.pending``) runs the queue entries through the same
  payload builder, so ``choices`` survive a reconnect. Without that, a reconnecting
  client re-derived the buttons itself and re-offered Session/Always for a scope the
  requester had withheld;
- the card's argument blob is run through the shared redactor (JSON-shaped secrets
  the error-string patterns miss) and capped.
"""

from unittest.mock import patch

from tools import approval as approval_mod
from tools import approval_prompt
from tools import mcp_tool
from tools import mcp_tool_handlers as _mcp_handlers


class TestTrustElicitationButtons:
    def test_gateway_payload_disables_session_and_permanent(self, monkeypatch):
        captured = {}

        def _fake_await(session_key, notify_cb, approval_data, **kwargs):
            captured.update(approval_data)
            return {"resolved": True, "choice": "once"}

        monkeypatch.setattr(approval_prompt._ctx, "get_current_session_key", lambda: "s")
        monkeypatch.setattr(approval_prompt._ctx, "_is_gateway_approval_context", lambda: True)
        monkeypatch.setattr(approval_prompt._gw, "_await_gateway_decision", _fake_await)
        monkeypatch.setattr(approval_mod, "_gateway_notify_cb", lambda session_key: (lambda *a, **k: None))

        assert approval_prompt.request_elicitation_consent("m", "d") == "accept"
        assert captured.get("allow_session") is False, captured
        assert captured.get("allow_permanent") is False, captured

    def test_cli_prompt_hides_session(self, monkeypatch):
        captured = {}

        def _fake_prompt(message, description, **kwargs):
            captured.update(kwargs)
            return "deny"

        monkeypatch.setattr(approval_prompt._ctx, "get_current_session_key", lambda: "s")
        monkeypatch.setattr(approval_prompt._ctx, "_is_gateway_approval_context", lambda: False)
        monkeypatch.setattr(approval_prompt, "prompt_dangerous_approval", _fake_prompt)

        assert approval_prompt.request_elicitation_consent("m", "d") == "decline"
        assert captured.get("allow_session") is False, captured
        assert captured.get("allow_permanent") is False, captured


class TestTrustGateCardArgs:
    def _gate(self, args):
        captured = {}

        def _consent(message, description, **kwargs):
            captured["message"] = message
            captured["description"] = description
            return "decline"

        handler = _mcp_handlers._make_tool_handler("srv", "rebuild_build", 30.0)
        with patch.dict(mcp_tool._server_trust_levels, {"srv": "untrusted"}), \
             patch.dict(mcp_tool._tool_read_only_hints, {}, clear=True), \
             patch("tools.approval_prompt.request_elicitation_consent", _consent):
            handler(dict(args))
        return captured

    def test_card_shows_call_arguments(self):
        captured = self._gate({"job": "deploy-prod", "params": {"n": 1}})
        blob = captured.get("message", "") + captured.get("description", "")
        assert "deploy-prod" in blob, captured

    def test_card_truncates_huge_arguments(self):
        captured = self._gate({"job": "x", "blob": "y" * 5000})
        blob = captured.get("message", "") + captured.get("description", "")
        assert "truncated" in blob.lower(), captured
        assert len(blob) < 4000, len(blob)

    def test_card_redacts_json_shaped_secrets(self):
        """The error-string redactor misses ``"api_key": "..."``; the shared one must not."""
        captured = self._gate({"api_key": "supersecretvalue123xyz", "job": "deploy-prod"})
        blob = captured.get("message", "") + captured.get("description", "")
        assert "supersecretvalue123xyz" not in blob, blob


class TestApprovalReplayCarriesChoices:
    def test_pending_replay_computes_choices(self):
        """``approval.pending`` must return the same ``choices`` the push path computes."""
        from tools.approval_gateway_wait import _ApprovalEntry
        from tui_gateway import methods_prompt

        entry = _ApprovalEntry({
            "command": "MCP tool 'rebuild' on UNTRUSTED server 'srv' wants to run.",
            "description": "Approve once or deny.",
            "pattern_key": "mcp_elicitation",
            "pattern_keys": ["mcp_elicitation"],
            "allow_session": False,
            "allow_permanent": False,
        })
        with patch.dict(approval_mod._gateway_queues, {"s": [entry]}, clear=True):
            payloads = methods_prompt._pending_approval_payloads("s")

        assert payloads[0]["choices"] == ["once", "deny"], payloads[0]
