"""Noninteractive provider-scoped model discovery (issue #105413, slice 1).

Catalog-only: builds the ``hermes models --json`` envelope from the bundled
static registry (``hermes_cli.models``). No network, no cache writes, no
credentials touched — safe for scripts and external integrations.

Exit codes: 0 result (warnings/fallbacks allowed), 2 bad args / unknown
provider, 3 known provider with no usable result, 4 unexpected failure.

# ponytail: catalog source only; live --refresh discovery and per-provider
disk cache are later slices (entries carry a refresh-fallback warning).
# ponytail: capability/modality fields are empty until a metadata source
lands; schema keys are stable so enrichers only fill values.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

SCHEMA_VERSION = "1"

_CODE_REFRESH_REQUIRES_PROVIDER = "refresh_requires_provider"
_CODE_REFRESH_OFFLINE_CONFLICT = "refresh_offline_conflict"
_CODE_UNSUPPORTED_PROVIDER = "unsupported_provider"
_CODE_NO_RESULT = "no_result"
_CODE_INTERNAL = "internal"


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_provider_id(raw: Any) -> Optional[str]:
    """Canonical slug for ``raw`` (aliases resolved), or None if unregistered."""
    from hermes_cli.models import CANONICAL_PROVIDERS, _PROVIDER_ALIASES

    slug = str(raw or "").strip().lower()
    if not slug:
        return None
    slug = _PROVIDER_ALIASES.get(slug, slug)
    if any(p.slug == slug for p in CANONICAL_PROVIDERS):
        return slug
    return None


def _catalog_models(provider: str) -> list[str]:
    from hermes_cli.models import _PROVIDER_MODELS

    models = _PROVIDER_MODELS.get(provider) or []
    return [m for m in models if isinstance(m, str) and m]


def _model_entry(model_id: str) -> dict[str, Any]:
    return {
        "id": model_id,
        "capabilities": [],
        "input_modalities": [],
        "output_modalities": [],
        "deprecated": False,
    }


def _error(code: str, message: str, provider: Optional[str] = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if provider is not None:
        err["provider"] = provider
    return err


def discover_models(
    provider: Optional[str] = None,
    refresh: bool = False,
    offline: bool = False,
) -> tuple[dict[str, Any], int]:
    """Build the discovery envelope. Never raises; never touches the network."""
    request = {"provider": provider, "refresh": bool(refresh), "offline": bool(offline)}

    def envelope(providers: list, errors: list, code: int) -> tuple[dict[str, Any], int]:
        return (
            {
                "schema_version": SCHEMA_VERSION,
                "request": request,
                "providers": providers,
                "errors": errors,
            },
            code,
        )

    if refresh and offline:
        return envelope([], [_error(
            _CODE_REFRESH_OFFLINE_CONFLICT,
            "--refresh and --offline are mutually exclusive.")], 2)
    if refresh and not provider:
        return envelope([], [_error(
            _CODE_REFRESH_REQUIRES_PROVIDER,
            "--refresh requires --provider to avoid broad network fan-out.")], 2)

    if provider is not None:
        slug = normalize_provider_id(provider)
        if slug is None:
            return envelope([], [_error(
                _CODE_UNSUPPORTED_PROVIDER,
                f"unsupported provider: {provider}", provider=str(provider))], 2)
        ids = _catalog_models(slug)
        if not ids:
            return envelope([], [_error(
                _CODE_NO_RESULT,
                f"no usable result for provider: {slug}", provider=slug)], 3)
        warnings = (
            ["live refresh unavailable in this build; returning catalog result."]
            if refresh else []
        )
        return envelope([{
            "id": slug,
            "source": "catalog",
            "retrieved_at": _utcnow(),
            "stale": False,
            "models": [_model_entry(m) for m in ids],
            "warnings": warnings,
        }], [], 0)

    from hermes_cli.models import CANONICAL_PROVIDERS

    providers = []
    for entry in CANONICAL_PROVIDERS:
        ids = _catalog_models(entry.slug)
        if not ids:
            continue
        providers.append({
            "id": entry.slug,
            "source": "catalog",
            "retrieved_at": _utcnow(),
            "stale": False,
            "models": [_model_entry(m) for m in ids],
            "warnings": [],
        })
    if not providers:
        return envelope([], [_error(_CODE_NO_RESULT, "no usable result.")], 3)
    return envelope(providers, [], 0)
