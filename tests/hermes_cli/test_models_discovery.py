"""RED tests for #105413: noninteractive provider-scoped JSON model discovery.

Slice 1 (this file): catalog-only `discover_models` + `hermes models --json`
argument validation. No network, no cache writes. Live refresh / disk cache
are later slices.
"""

import argparse
import json

import pytest

from hermes_cli.models_discovery import (
    SCHEMA_VERSION,
    discover_models,
    normalize_provider_id,
)


def _envelope(**kw):
    resp, code = discover_models(**kw)
    # Must survive a stdout round-trip as exactly one JSON document.
    doc = json.loads(json.dumps(resp))
    assert isinstance(doc, dict)
    return doc, code


class TestRequestValidation:
    def test_refresh_without_provider_is_arg_error(self):
        doc, code = _envelope(provider=None, refresh=True, offline=False)
        assert code == 2
        assert doc["schema_version"] == SCHEMA_VERSION
        assert doc["providers"] == []
        assert doc["errors"][0]["code"] == "refresh_requires_provider"

    def test_refresh_and_offline_conflict(self):
        doc, code = _envelope(provider="nous", refresh=True, offline=True)
        assert code == 2
        assert doc["errors"][0]["code"] == "refresh_offline_conflict"

    def test_unknown_provider_is_arg_error(self):
        doc, code = _envelope(provider="no-such-provider", refresh=False, offline=False)
        assert code == 2
        assert doc["errors"][0]["code"] == "unsupported_provider"

    def test_alias_resolves_to_canonical(self):
        assert normalize_provider_id("claude") == "anthropic"
        doc, code = _envelope(provider="claude", refresh=False, offline=False)
        assert code == 0
        assert doc["providers"][0]["id"] == "anthropic"


class TestCatalogResult:
    def test_single_provider_envelope_shape(self):
        doc, code = _envelope(provider="nous", refresh=False, offline=False)
        assert code == 0
        assert doc["schema_version"] == "1"
        assert doc["request"] == {"provider": "nous", "refresh": False, "offline": False}
        (entry,) = doc["providers"]
        assert entry["id"] == "nous"
        assert entry["source"] == "catalog"
        assert entry["warnings"] == []
        assert len(entry["models"]) > 0
        for m in entry["models"]:
            assert set(("id", "capabilities", "input_modalities",
                        "output_modalities", "deprecated")) <= set(m)

    def test_default_lists_registered_providers_without_network(self, monkeypatch):
        import socket
        import urllib.request

        def _boom(*a, **k):
            raise AssertionError("network used during default discovery")

        monkeypatch.setattr(socket, "create_connection", _boom)
        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        doc, code = _envelope(provider=None, refresh=False, offline=False)
        assert code == 0
        assert len(doc["providers"]) > 1
        assert {p["source"] for p in doc["providers"]} == {"catalog"}

    def test_offline_never_touches_network(self, monkeypatch):
        import socket
        import urllib.request

        def _boom(*a, **k):
            raise AssertionError("network used in offline mode")

        monkeypatch.setattr(socket, "create_connection", _boom)
        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        doc, code = _envelope(provider="nous", refresh=False, offline=True)
        assert code == 0
        assert doc["providers"][0]["source"] == "catalog"

    def test_refresh_falls_back_to_catalog_with_warning(self):
        doc, code = _envelope(provider="nous", refresh=True, offline=False)
        assert code == 0
        (entry,) = doc["providers"]
        assert entry["source"] == "catalog"
        assert any("refresh" in w.lower() for w in entry["warnings"])

    def test_known_provider_without_catalog_models_is_no_result(self):
        doc, code = _envelope(provider="openrouter", refresh=False, offline=False)
        assert code == 3
        assert doc["providers"] == []
        assert doc["errors"][0]["code"] == "no_result"

    def test_default_listing_reports_registered_providers_without_models(self):
        """Residual of the review: the provider-less branch used to `continue` past
        registered-but-unbundled slugs, so 15 providers (incl. `openrouter`, `custom`
        and the *-oauth/local runtimes) vanished with no error and no warning."""
        doc, code = _envelope(provider=None, refresh=False, offline=False)
        assert code == 0
        from hermes_cli.models import CANONICAL_PROVIDERS

        all_slugs = {entry.slug for entry in CANONICAL_PROVIDERS}
        emitted = {p["id"] for p in doc["providers"]}
        reported = {e["provider"] for e in doc["errors"] if e.get("code") == "no_result"}
        # Every registered provider is either listed or explicitly reported — none drops silently.
        assert emitted | reported == all_slugs
        assert not (emitted & reported)
        assert reported  # this catalog slice really does have registered-but-unbundled slugs
        assert "openrouter" not in emitted and "openrouter" in reported
        assert all(e.get("message") for e in doc["errors"])


class TestRedaction:
    def test_envelope_has_no_secret_surface(self):
        doc, code = _envelope(provider=None, refresh=False, offline=False)
        forbidden = {"api_key", "apikey", "token", "secret", "bearer",
                     "endpoint", "base_url", "account", "quota", "billing",
                     "headers", "auth", "credentials", "raw"}
        seen_keys = set()

        def _walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    seen_keys.add(str(k).lower())
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)

        _walk(doc)
        assert not (seen_keys & forbidden), seen_keys & forbidden
        blob = json.dumps(doc)
        for needle in ('"token":', '"api_key":', '"secret":', '"bearer"',
                       '"base_url":', '"endpoint":'):
            assert needle not in blob.lower()


class TestParser:
    def _parser(self):
        from hermes_cli.subcommands.models import build_models_parser

        parser = argparse.ArgumentParser(prog="hermes")
        sub = parser.add_subparsers(dest="command")
        build_models_parser(sub, cmd_models=lambda a: 0)
        return parser

    def test_json_provider_parses(self):
        args = self._parser().parse_args(["models", "--json", "--provider", "nous"])
        assert args.json is True and args.provider == "nous"
        assert args.refresh is False and args.offline is False

    def test_refresh_offline_flags_parse(self):
        args = self._parser().parse_args(
            ["models", "--json", "--provider", "nous", "--refresh"])
        assert args.refresh is True
        args = self._parser().parse_args(["models", "--json", "--offline"])
        assert args.offline is True
