"""#103809 repro: `-s/--skills` must reach the model on the `hermes chat -q` path.

The -q quiet runner (cli._run_single_query_mode) builds the agent via
_init_agent(), which joins the background skills-preload thread and folds the
skill text into cli.system_prompt -> AIAgent(ephemeral_system_prompt=...).
A recorder standing in for AIAgent proves the canary token is delivered to the
agent constructor — the only place the CLI can still silently drop it.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


def _install_fake_aiagent(monkeypatch, captured):
    """Swap run_agent.AIAgent for a recorder capturing constructor kwargs."""
    class _Recorder:
        def __init__(self, *args, **kwargs):
            captured["init_kwargs"] = dict(kwargs)

        def run_conversation(self, user_message, conversation_history=None):
            return {"final_response": "4", "messages": [], "api_calls": 1}

        def close(self):
            pass

    fake = types.ModuleType("run_agent")
    fake.AIAgent = _Recorder
    monkeypatch.setitem(sys.modules, "run_agent", fake)


def _import_cli_with_stubs():
    import cli as cli_mod

    clean_config = {
        "model": {"default": "anthropic/claude-opus-4.6",
                  "base_url": "https://openrouter.ai/api/v1", "provider": "auto"},
        "display": {"compact": False, "tool_progress": "all"},
        "agent": {},
        "terminal": {"env_type": "local"},
    }
    prompt_toolkit_stubs = {f"prompt_toolkit{s}": MagicMock() for s in (
        "", ".history", ".styles", ".patch_stdout", ".application", ".layout",
        ".layout.processors", ".filters", ".layout.dimension", ".layout.menus",
        ".widgets", ".key_binding", ".completion", ".formatted_text")}
    with patch.dict(sys.modules, prompt_toolkit_stubs), patch.dict(
        "os.environ", {"LLM_MODEL": "", "HERMES_MAX_ITERATIONS": ""}, clear=False
    ):
        cli_mod = __import__("importlib").reload(cli_mod)
        with patch.dict(cli_mod.__dict__, {"CLI_CONFIG": clean_config}):
            return cli_mod


def test_chat_q_skill_reaches_agent_ephemeral_prompt(monkeypatch):
    captured = {}
    _install_fake_aiagent(monkeypatch, captured)
    cli_mod = _import_cli_with_stubs()
    monkeypatch.setattr(
        cli_mod, "build_preloaded_skills_prompt",
        lambda skills, task_id=None: (
            "Probe skill: when asked anything reply exactly ZEBRAFISH-8891",
            ["canary-probe"], []),
    )
    monkeypatch.setattr(cli_mod.HermesCLI, "_claim_active_session", lambda self, *a, **k: True)
    monkeypatch.setattr(cli_mod.HermesCLI, "_ensure_runtime_credentials", lambda self: True)
    monkeypatch.setattr(cli_mod, "_prepare_deferred_agent_startup", lambda *a, **k: None)
    monkeypatch.setattr(cli_mod.HermesCLI, "_install_tool_callbacks", lambda self: None)
    monkeypatch.setattr(cli_mod.HermesCLI, "_ensure_tirith_security", lambda self: None)
    monkeypatch.setattr(cli_mod, "_install_single_query_signal_handlers", lambda cli: None)
    monkeypatch.setattr(cli_mod, "_run_cleanup", lambda *a, **k: None)
    from hermes_cli import mcp_startup
    monkeypatch.setattr(mcp_startup, "ensure_mcp_discovery_before_agent_build",
                        lambda *a, **k: None)

    with pytest.raises(SystemExit):
        cli_mod.main(skills="canary-probe", toolsets="clarify,todo",
                     query="What is 2+2?", quiet=True)

    ephemeral = captured["init_kwargs"].get("ephemeral_system_prompt") or ""
    assert "ZEBRAFISH-8891" in ephemeral, (
        "canary skill text missing from AIAgent ephemeral_system_prompt "
        f"(got {ephemeral[:120]!r})")
