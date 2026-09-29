"""Regression test for #105979: agent rebuilt on every turn after /model switch.

When the session model (via /model) routes to a different api_mode than the
config default, _ensure_runtime_credentials() must resolve the runtime from
the session model, not the config default. Otherwise self.api_mode is
overwritten every turn -> routing/model change detected -> self.agent=None.
Uses the REAL resolver (no mocks) so this is a true red/green test.
"""

from __future__ import annotations

from types import SimpleNamespace

from hermes_cli.cli_agent_setup_mixin import CLIAgentSetupMixin


class _ModelSwitchedCLI(CLIAgentSetupMixin):
    """Post-/model-switch state: session on qwen (anthropic_messages)."""

    def __init__(self):
        self.model = "qwen3.7-max"
        self.requested_provider = "opencode-go"
        self.provider = "opencode-go"
        self.api_key = "test-key"
        self.base_url = "https://opencode.ai/zen/go"
        self.api_mode = "anthropic_messages"
        self.acp_command = None
        self.acp_args = []
        self.agent = SimpleNamespace()  # existing agent from prior turn
        self._active_agent_route_signature = ("sig",)
        self._fallback_model = []
        self._explicit_api_key = None
        self._explicit_base_url = None
        self._credential_pool = None
        self._provider_source = None
        self._model_is_default = False

    def _console_print(self, *a, **k):
        pass

    def _normalize_model_for_provider(self, resolved_provider: str) -> bool:
        import cli as cli_mod

        return cli_mod.HermesCLI._normalize_model_for_provider(
            self, resolved_provider
        )


def _write_config():
    from hermes_constants import get_hermes_home

    (get_hermes_home() / "config.yaml").write_text(
        "model:\n"
        "  default: glm-5.3-flash\n"
        "  provider: opencode-go\n"
        "providers:\n"
        "  opencode-go:\n"
        "    base_url: https://opencode.ai/zen/go\n"
        "    api_key: test-key\n",
        encoding="utf-8",
    )


def test_resolver_route_depends_on_session_model(monkeypatch):
    """Premise: opencode-go routes glm->chat and qwen->anthropic (real resolver)."""
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    _write_config()
    from hermes_cli.runtime_provider import resolve_runtime_provider

    default_route = resolve_runtime_provider(requested="opencode-go")
    session_route = resolve_runtime_provider(
        requested="opencode-go", target_model="qwen3.7-max"
    )
    assert default_route["api_mode"] == "chat_completions"
    assert session_route["api_mode"] == "anthropic_messages"


def test_ensure_runtime_credentials_preserves_session_route(monkeypatch):
    """RED on main: per-turn refresh must not tear down a healthy agent."""
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    _write_config()
    cli = _ModelSwitchedCLI()
    before = cli.agent
    assert cli._ensure_runtime_credentials() is True
    assert cli.api_mode == "anthropic_messages", (
        f"api_mode overwritten to {cli.api_mode!r}; agent rebuilt every turn (#105979)"
    )
    assert cli.agent is before, "agent torn down despite unchanged route (#105979)"
