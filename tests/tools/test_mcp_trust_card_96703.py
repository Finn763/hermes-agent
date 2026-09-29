"""RED tests for #96703: trust-gate card shows args; Session/Always hidden."""

from unittest.mock import patch

from tools import approval as approval_mod
from tools import mcp_tool


def _gateway_capture():
    captured = {}

    def _await(session_key, notify_cb, approval_data, surface="gateway"):
        captured.update(approval_data)
        return {"resolved": True, "choice": "once"}

    return captured, _await


class TestTrustElicitationButtons:
    def test_gateway_payload_disables_session_and_permanent(self):
        captured, fake_await = _gateway_capture()
        with patch.object(approval_mod, "get_current_session_key", return_value="s"), \
             patch.object(approval_mod, "_is_gateway_approval_context", return_value=True), \
             patch.dict(approval_mod._gateway_notify_cbs, {"s": lambda *a, **k: None}), \
             patch.object(approval_mod, "_await_gateway_decision", side_effect=fake_await):
            assert approval_mod.request_elicitation_consent("m", "d") == "accept"
        assert captured.get("allow_session") is False, captured
        assert captured.get("allow_permanent") is False, captured


class TestTrustGateCardArgs:
    def _gate(self, args):
        mcp_tool._server_trust_levels["srv"] = "untrusted"
        handler = mcp_tool._make_tool_handler("srv", "rebuild_build", 30.0)
        captured = {}

        def _consent(message, description, **kwargs):
            captured["message"] = message
            captured["description"] = description
            return "decline"

        try:
            with patch.dict(mcp_tool._server_trust_levels, {"srv": "untrusted"}), \
                 patch.dict(mcp_tool._tool_read_only_hints, {}, clear=True), \
                 patch("tools.approval.request_elicitation_consent", _consent):
                handler(dict(args))
        finally:
            mcp_tool._server_trust_levels.pop("srv", None)
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
