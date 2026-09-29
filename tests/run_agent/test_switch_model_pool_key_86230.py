"""Regression test for #86230: switch_model must load the canonical pool key.

A named custom provider (e.g. ``sensenova``) stores credentials under
``custom:sensenova``. Loading the pool with the bare name yields an empty
pool and silently disables 429/401 rotation after every model switch.
"""

from unittest.mock import MagicMock, patch

from agent.agent_runtime_helpers import switch_model


def _make_agent(provider, model, pool):
    agent = MagicMock(name="Agent")
    agent.provider = provider
    agent.model = model
    agent.requested_provider = provider
    agent.base_url = "https://old.example/v1"
    agent.api_key = "old-key"
    agent.api_mode = "chat_completions"
    agent.client = MagicMock()
    agent._client_kwargs = {"api_key": "***", "base_url": "https://old.example/v1"}
    agent._anthropic_client = None
    agent._anthropic_api_key = ""
    agent._anthropic_base_url = None
    agent._is_anthropic_oauth = False
    agent._config_context_length = None
    agent._transport_cache = {}
    agent._cached_system_prompt = "x"
    agent.context_compressor = None
    agent._use_prompt_caching = False
    agent._use_native_cache_layout = False
    agent._primary_runtime = {}
    agent._fallback_activated = False
    agent._fallback_index = 0
    agent._fallback_chain = []
    agent._fallback_model = None
    agent._credential_pool = pool
    agent._credential_pool_entry_id = None
    agent._anthropic_prompt_cache_policy = MagicMock(return_value=(False, False))
    agent._ensure_lmstudio_runtime_loaded = MagicMock()
    return agent


class TestSwitchModelCanonicalPoolKey:
    def test_named_custom_loads_scoped_key(self):
        old_pool = MagicMock()
        old_pool.provider = "openai"
        agent = _make_agent("openai", "gpt-4o", old_pool)
        new_base = "https://sensenova.example/v1"
        configured = [
            (
                "sensenova",
                {
                    "name": "Sensenova",
                    "provider_key": "sensenova",
                    "base_url": new_base,
                },
            )
        ]
        with (
            patch(
                "agent.credential_pool._iter_custom_providers",
                return_value=configured,
            ),
            patch("agent.credential_pool.load_pool") as load_pool_mock,
        ):
            load_pool_mock.side_effect = lambda key: MagicMock(provider=key)
            switch_model(
                agent,
                new_model="m1",
                new_provider="sensenova",
                api_key="k1",
                base_url=new_base,
                api_mode="chat_completions",
            )
        load_pool_mock.assert_called_once_with("custom:sensenova")
        assert agent._credential_pool.provider == "custom:sensenova"

    def test_plain_provider_key_unchanged(self):
        old_pool = MagicMock()
        old_pool.provider = "openai"
        agent = _make_agent("openai", "gpt-4o", old_pool)
        with (
            patch("agent.credential_pool._iter_custom_providers", return_value=[]),
            patch("agent.credential_pool.load_pool") as load_pool_mock,
        ):
            load_pool_mock.side_effect = lambda key: MagicMock(provider=key)
            switch_model(
                agent,
                new_model="llama-3.3-70b",
                new_provider="groq",
                api_key="groq-key",
                base_url="https://api.groq.com/openai/v1",
                api_mode="chat_completions",
            )
        load_pool_mock.assert_called_once_with("groq")
