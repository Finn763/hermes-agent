"""RED test for #93988: session /model override evicts model.default.

Setup: configured default A on provider pa, fallback_providers [B, C].
Session overrides to X on px. X fails -> recovery must try A first:
expected chain X -> A -> B -> C, actual on main X -> B -> C.
"""

from unittest.mock import MagicMock, patch

from hermes_cli.fallback_config import seat_configured_default_at_head
from run_agent import AIAgent


def _make_agent(model, provider, chain):
    agent = AIAgent.__new__(AIAgent)
    agent.model = model
    agent.provider = provider
    agent.requested_provider = provider
    agent.base_url = "https://pa.example/v1"
    agent.api_key = "pa-key"
    agent.api_mode = "chat_completions"
    agent.client = MagicMock()
    agent._client_kwargs = {"api_key": "pa-key", "base_url": "https://pa.example/v1"}
    agent.context_compressor = None
    agent._anthropic_api_key = ""
    agent._anthropic_base_url = None
    agent._anthropic_client = None
    agent._is_anthropic_oauth = False
    agent._cached_system_prompt = "cached"
    agent._primary_runtime = {}
    agent._fallback_activated = False
    agent._fallback_index = 0
    agent._fallback_chain = list(chain)
    agent._fallback_model = chain[0] if chain else None
    agent._create_openai_client = MagicMock(return_value=MagicMock())
    agent._apply_client_headers_for_base_url = MagicMock()
    return agent


def _switch(agent, model, provider):
    with patch(
        "hermes_cli.timeouts.get_provider_request_timeout", return_value=None
    ):
        agent.switch_model(
            new_model=model,
            new_provider=provider,
            api_key="x-key",
            base_url="https://x.example/v1",
            api_mode="chat_completions",
        )


def test_override_switch_seats_configured_default_at_chain_head():
    agent = _make_agent("a-model", "pa", [
        {"provider": "pb", "model": "b-model"},
        {"provider": "pc", "model": "c-model"},
    ])
    fake_model_cfg = {"default": "a-model", "provider": "pa", "base_url": ""}
    with patch(
        "hermes_cli.runtime_provider._get_model_config", return_value=fake_model_cfg
    ):
        _switch(agent, "x-model", "px")

    head = agent._fallback_chain[0]
    assert (head.get("provider"), head.get("model")) == ("pa", "a-model"), (
        "override X must recover via configured default A first, got chain %r"
        % (agent._fallback_chain,)
    )
    assert agent._fallback_model == head


def test_primary_equals_default_leaves_chain_untouched():
    agent = _make_agent("a-model", "pa", [
        {"provider": "pb", "model": "b-model"},
    ])
    fake_model_cfg = {"default": "a-model", "provider": "pa", "base_url": ""}
    with patch(
        "hermes_cli.runtime_provider._get_model_config", return_value=fake_model_cfg
    ):
        _switch(agent, "a-model", "pa")

    assert agent._fallback_chain == [{"provider": "pb", "model": "b-model"}]


def test_helper_seats_default_head_and_dedupes():
    chain = [
        {"provider": "pb", "model": "b-model"},
        {"provider": "pa", "model": "a-model"},
    ]
    out = seat_configured_default_at_head(
        chain,
        primary_model="x-model",
        primary_provider="px",
        default_model="a-model",
        default_provider="pa",
    )
    assert out == [
        {"provider": "pa", "model": "a-model"},
        {"provider": "pb", "model": "b-model"},
    ]
    # input untouched
    assert len(chain) == 2 and chain[1]["provider"] == "pa"


def test_helper_noop_when_primary_is_default():
    chain = [{"provider": "pb", "model": "b-model"}]
    out = seat_configured_default_at_head(
        chain,
        primary_model="a-model",
        primary_provider="pa",
        default_model="a-model",
        default_provider="pa",
    )
    assert out == chain


def test_helper_noop_without_usable_default():
    chain = [{"provider": "pb", "model": "b-model"}]
    assert (
        seat_configured_default_at_head(
            chain,
            primary_model="x-model",
            primary_provider="px",
            default_model="",
            default_provider="",
        )
        == chain
    )
