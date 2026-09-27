"""RED tests for #105353: hindsight_recall/reflect must support per-call bank
targeting and per-hit provenance (bank / document id / source)."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.memory.hindsight import (
    HindsightMemoryProvider,
    RECALL_SCHEMA,
    REFLECT_SCHEMA,
)


@pytest.fixture(autouse=True)
def _clean_env(tmp_path, monkeypatch):
    for key in ("HINDSIGHT_API_KEY", "HINDSIGHT_BANK_ID", "HINDSIGHT_BUDGET",
                "HINDSIGHT_MODE", "HINDSIGHT_API_URL"):
        monkeypatch.delenv(key, raising=False)
    isolated_home = tmp_path / "user-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: isolated_home))


def _make_provider(tmp_path, monkeypatch, **overrides):
    config = {
        "mode": "cloud",
        "apiKey": "test-key",
        "api_url": "http://localhost:9999",
        "bank_id": "test-bank",
        "budget": "mid",
        "memory_mode": "hybrid",
    }
    config.update(overrides)
    config_path = tmp_path / "hindsight" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(
        "plugins.memory.hindsight.get_hermes_home", lambda: tmp_path
    )
    p = HindsightMemoryProvider()
    p.initialize(session_id="test-session", hermes_home=str(tmp_path), platform="cli")
    captured = {}

    async def _arecall(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(results=[
            SimpleNamespace(text="curated fact", document_id="doc-123", source="curated-src"),
            SimpleNamespace(text="plain fact"),
        ])

    async def _areflect(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text="synthesized")

    client = MagicMock()
    client.arecall = AsyncMock(side_effect=_arecall)
    client.areflect = AsyncMock(side_effect=_areflect)
    client.aretain_batch = AsyncMock()
    client.aclose = AsyncMock()
    p._client = client
    return p, captured


def test_schemas_expose_optional_bank():
    for schema in (RECALL_SCHEMA, REFLECT_SCHEMA):
        assert "bank" in schema["parameters"]["properties"]
        assert "bank" not in schema["parameters"].get("required", [])


def test_tool_recall_targets_named_bank(tmp_path, monkeypatch):
    p, captured = _make_provider(tmp_path, monkeypatch)
    out = json.loads(p.handle_tool_call("hindsight_recall", {"query": "q", "bank": "other-bank"}))
    assert "result" in out, out
    assert captured.get("bank_id") == "other-bank"


def test_tool_reflect_targets_named_bank(tmp_path, monkeypatch):
    p, captured = _make_provider(tmp_path, monkeypatch)
    out = json.loads(p.handle_tool_call("hindsight_reflect", {"query": "q", "bank": "other-bank"}))
    assert "result" in out, out
    assert captured.get("bank_id") == "other-bank"


def test_tool_recall_disallowed_bank_is_visible_error(tmp_path, monkeypatch):
    p, captured = _make_provider(
        tmp_path, monkeypatch, recall_bank_allowlist=["test-bank"])
    raw = p.handle_tool_call("hindsight_recall", {"query": "q", "bank": "evil-bank"})
    out = json.loads(raw)
    assert "error" in out, out
    assert "evil-bank" in out["error"]
    assert captured == {}, "disallowed bank must never reach the client"


def test_tool_recall_includes_provenance(tmp_path, monkeypatch):
    p, _ = _make_provider(tmp_path, monkeypatch)
    out = json.loads(p.handle_tool_call("hindsight_recall", {"query": "q"}))
    text = out["result"]
    assert "doc-123" in text
    assert "curated-src" in text
    assert "test-bank" in text
