"""Regression test for issue #56995.

Text-only main model on an aggregator provider (e.g. GLM 5.2 served via
OpenRouter, or any text-only ``openrouter`` chat model) + ``auxiliary.vision``
at defaults (``provider: auto``) + ``OPENROUTER_API_KEY`` set must still
resolve a vision client via the OpenRouter fallback.

Before the fix, ``resolve_vision_provider_client(provider="auto")`` skipped
the text-only main model (correct) but then ALSO skipped ``openrouter`` in
the aggregator loop (``if candidate == main_provider: continue``) — even
though the loop would have tried OpenRouter with its own *default vision
model*, not the text-only chat model step 1 rejected. Result: ``(None, None,
None)`` despite a working ``OPENROUTER_API_KEY``, and the turn died in
``call_llm(task="vision")`` with ``No LLM provider configured``.

No network: capability lookup is stubbed to report the main model as
text-only; the OpenRouter client build only reads env + constructs an SDK
object.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest


@pytest.fixture
def isolated_home(monkeypatch):
    test_home = tempfile.mkdtemp(prefix="hermes_test_56995_")
    hermes_home = os.path.join(test_home, ".hermes")
    os.makedirs(hermes_home)
    monkeypatch.setenv("HERMES_HOME", hermes_home)
    for k in list(os.environ.keys()):
        if k.endswith("_API_KEY") or k.endswith("_TOKEN"):
            monkeypatch.delenv(k, raising=False)
    yield hermes_home
    shutil.rmtree(test_home, ignore_errors=True)


@pytest.fixture
def restore_health():
    """Snapshot/restore the aux unhealthy-provider marks (failure paths mark)."""
    import agent.auxiliary_client as aux

    saved = dict(aux._aux_unhealthy_until)
    saved_logged = dict(aux._aux_unhealthy_logged_at)
    yield
    aux._aux_unhealthy_until.clear()
    aux._aux_unhealthy_until.update(saved)
    aux._aux_unhealthy_logged_at.clear()
    aux._aux_unhealthy_logged_at.update(saved_logged)


def test_text_only_main_on_openrouter_still_uses_openrouter_fallback(
    isolated_home, monkeypatch, restore_health
):
    """RED (#56995): auto vision must land on OpenRouter, not None."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")

    import agent.auxiliary_client as aux

    # Simulate a text-only main model served via OpenRouter (e.g. GLM 5.2
    # class): capability lookup reports no vision, so step 1 must skip it.
    monkeypatch.setattr(aux, "_main_model_supports_vision", lambda p, m: False)

    runtime = {"provider": "openrouter", "model": "z-ai/glm-5.2-text-only"}
    provider, client, model = aux.resolve_vision_provider_client(
        provider="auto", main_runtime=runtime
    )
    assert client is not None, (
        "vision auto-detect skipped text-only main model but never tried the "
        "OpenRouter fallback even though OPENROUTER_API_KEY is set (#56995)"
    )
    assert provider == "openrouter"


def test_text_only_main_without_any_aggregator_still_none(
    isolated_home, monkeypatch, restore_health
):
    """Guard: no credentials anywhere must still resolve to nothing.

    The fix must not invent a client — it only stops skipping a provider
    whose own default vision model was never attempted.
    """
    import agent.auxiliary_client as aux

    monkeypatch.setattr(aux, "_main_model_supports_vision", lambda p, m: False)

    runtime = {"provider": "openrouter", "model": "z-ai/glm-5.2-text-only"}
    provider, client, model = aux.resolve_vision_provider_client(
        provider="auto", main_runtime=runtime
    )
    assert client is None
    assert provider is None
