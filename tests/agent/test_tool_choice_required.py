"""Wire-level tool enforcement for OpenAI-compatible chat_completions.

``agent.tool_choice: required`` (config ``agent.tool_choice``) must surface as
``tool_choice="required"`` in the built chat_completions kwargs whenever tools
are present — this is the upstream form of the local patch in #105426 section 3
(models answering with instructions instead of invoking tools).
Default ``auto`` changes nothing.
"""

from __future__ import annotations

from unittest.mock import patch

from agent.chat_completion_helpers import build_api_kwargs
from run_agent import AIAgent

_MSGS = [{"role": "user", "content": "hi"}]
_TOOLS = [{"type": "function", "function": {
    "name": "terminal", "description": "run a command",
    "parameters": {"type": "object", "properties": {}}}}]


def _agent():
    a = AIAgent(
        api_key="test-key-1234567890",
        base_url="http://127.0.0.1:9/v1",
        model="test/model",
        provider="custom",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    a.api_mode = "chat_completions"
    a._transport = None
    return a


def test_required_sets_tool_choice_with_tools():
    a = _agent()
    a._tool_choice = "required"
    kwargs = build_api_kwargs(a, _MSGS, _TOOLS)
    assert kwargs["tool_choice"] == "required"
    assert kwargs["tools"] == _TOOLS


def test_default_auto_sets_nothing():
    a = _agent()
    a._tool_choice = "auto"
    assert "tool_choice" not in build_api_kwargs(a, _MSGS, _TOOLS)


def test_required_without_tools_sets_nothing():
    a = _agent()
    a._tool_choice = "required"
    assert "tool_choice" not in build_api_kwargs(a, _MSGS, [])


def test_explicit_request_override_wins():
    a = _agent()
    a._tool_choice = "required"
    a.request_overrides = {"tool_choice": "none"}
    assert build_api_kwargs(a, _MSGS, _TOOLS)["tool_choice"] == "none"


def test_config_plumbing():
    cfg = {"agent": {"tool_choice": "required"}}
    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("hermes_cli.config.load_config_readonly", return_value=cfg),
    ):
        a = AIAgent(
            api_key="test-key-1234567890",
            base_url="http://127.0.0.1:9/v1",
            model="test/model",
            provider="custom",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    assert a._tool_choice == "required"


def _anthropic_agent():
    a = _agent()
    a.api_mode = "anthropic_messages"
    return a


def test_required_reaches_the_anthropic_route():
    """The gate must cover anthropic_messages too: the adapter already maps
    ``required`` to ``{"type": "any"}``, but the caller never forwarded it, so the
    config was silently inert on that route."""
    a = _anthropic_agent()
    a._tool_choice = "required"
    assert build_api_kwargs(a, _MSGS, _TOOLS)["tool_choice"] == {"type": "any"}


def test_anthropic_default_stays_auto():
    a = _anthropic_agent()
    a._tool_choice = "auto"
    assert build_api_kwargs(a, _MSGS, _TOOLS)["tool_choice"] == {"type": "auto"}


def test_anthropic_required_without_tools_sets_nothing():
    a = _anthropic_agent()
    a._tool_choice = "required"
    kwargs = build_api_kwargs(a, _MSGS, [])
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs
