"""A read-only status/catalog read must not spend the pool's single-use refresh token.

``/model`` decides whether to render a provider row from the credential-pool usability gate
(``_credential_pool_is_usable`` → ``has_available()``), while the runtime resolver
(``resolve_codex_runtime_credentials`` / ``resolve_xai_oauth_runtime_credentials``) refreshes the
very same credential on demand.  When the status snapshot refreshed a stale pool entry on its own,
a failed *speculative* refresh latched and persisted an exhaustion status on that entry; every
credential-gated listing path then read the pool as unusable and dropped the provider row — the UI
falls back to the unconfigured skeleton ("ChatGPT or Codex Subscription · (needs setup)" /
"openai-codex · 0 models") and stays that way across gateway restarts, even though sessions on that
credential keep working.

The same contract covers observation of the pool itself: a status snapshot is not a lease, so it
must not count a selection, rotate round-robin priority, heal a cooldown, or persist any of that.
The persisted pool order is the user's credential-routing decision.
"""

import base64
import json
import time

import pytest

from agent import credential_pool
from agent.credential_pool import load_pool
from hermes_cli.auth import (
    AuthError,
    DEFAULT_CODEX_BASE_URL,
    get_codex_auth_status,
    get_xai_oauth_auth_status,
)


def _jwt_with_exp(offset_seconds: int) -> str:
    def _b64(payload: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=").decode("utf-8")

    return f"{_b64({'alg': 'none'})}.{_b64({'exp': int(time.time()) + offset_seconds})}.sig"


def _write_auth_store(home, payload: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"version": 1, **payload}), encoding="utf-8")


def _pool_only_codex_entry(index: int, access_token: str) -> dict:
    return {
        "id": f"codex-pool-entry-{index}",
        "label": f"device_code-{index}",
        "auth_type": "oauth",
        "source": "device_code",
        "priority": index,
        "request_count": 0,
        "access_token": access_token,
        "refresh_token": f"codex-refresh-token-{index}",
        "base_url": DEFAULT_CODEX_BASE_URL,
    }


def _write_pool_only_codex(home, *, access_tokens: list) -> None:
    """Auth store whose only Codex credentials live in the pool (no providers.openai-codex)."""
    _write_auth_store(home, {"credential_pool": {
        "openai-codex": [_pool_only_codex_entry(i, token) for i, token in enumerate(access_tokens)],
    }})


def _hermetic_codex_home(tmp_path, monkeypatch, *, access_tokens=None):
    """Pin HERMES_HOME/CODEX_HOME and keep every Codex path offline."""
    import hermes_cli.auth as auth
    import hermes_cli.codex_models as codex_models

    home = tmp_path / "hermes"
    # Default: one stale-but-refreshable credential.
    _write_pool_only_codex(home, access_tokens=access_tokens or [_jwt_with_exp(-3600)])
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex-cli"))
    # No live Codex catalog round-trip: the curated fallback is what a 401/offline picker shows.
    monkeypatch.setattr(codex_models, "_fetch_models_from_api", lambda access_token: [])
    # Deterministic, offline stand-in for a transient refresh failure (network blip, 429, restarted
    # token endpoint): the credential itself is still good — the runtime resolver refreshes it later.
    refresh_calls: list = []

    def _transient_failure(access_token, refresh_token, *args, **kwargs):
        refresh_calls.append((access_token, refresh_token))
        raise AuthError("Codex token refresh failed: temporary upstream error",
                        provider="openai-codex", code="codex_refresh_failed")
    monkeypatch.setattr(auth, "refresh_codex_oauth_pure", _transient_failure)
    return home, refresh_calls


def _hermetic_xai_home(tmp_path, monkeypatch):
    """Pin HERMES_HOME with an expiring xAI OAuth singleton in the auth store."""
    import hermes_cli.auth as auth

    home = tmp_path / "hermes"
    _write_auth_store(home, {"providers": {"xai-oauth": {
        "tokens": {"access_token": _jwt_with_exp(-3600), "refresh_token": "xai-refresh-token"},
        "last_refresh": "2026-01-01T00:00:00Z",
        "auth_mode": "oauth_device_code",
    }}})
    monkeypatch.setenv("HERMES_HOME", str(home))
    refresh_calls: list = []

    def _transient_failure(access_token, refresh_token, *args, **kwargs):
        refresh_calls.append((access_token, refresh_token))
        raise AuthError("xAI Token refresh failed: temporary upstream error",
                        provider="xai-oauth", code="xai_refresh_failed")
    monkeypatch.setattr(auth, "refresh_xai_oauth_pure", _transient_failure)
    return home, refresh_calls


def _persisted_codex_pool(home) -> list:
    persisted = json.loads((home / "auth.json").read_text(encoding="utf-8"))
    return persisted["credential_pool"]["openai-codex"]


def test_status_snapshot_does_not_spend_the_pool_refresh_token(tmp_path, monkeypatch):
    home, refresh_calls = _hermetic_codex_home(tmp_path, monkeypatch)

    status = get_codex_auth_status()

    assert refresh_calls == [], (
        "a read-only status snapshot must not spend the single-use pool refresh token; "
        f"attempted {len(refresh_calls)} refresh(es)"
    )
    assert status["logged_in"] is True, status
    # The gate every listing surface (picker, desktop chat picker, doctor) reads.
    assert load_pool("openai-codex").has_available() is True

    from hermes_cli.model_switch import list_authenticated_providers

    rows = [r for r in list_authenticated_providers(
        current_provider="openai-codex", current_model="gpt-5.6-sol") if r["slug"] == "openai-codex"]
    assert rows, "openai-codex row vanished from the picker after a status read"
    assert rows[0]["total_models"] > 0, rows[0]

    # ... and the poisoned status was not written to disk either (survives a gateway restart).
    for entry in _persisted_codex_pool(home):
        assert entry.get("last_status") in (None, "ok"), entry


def test_xai_status_snapshot_does_not_spend_the_pool_refresh_token(tmp_path, monkeypatch):
    """The helper is shared: the xAI half must not fall through to a refreshing resolver."""
    home, refresh_calls = _hermetic_xai_home(tmp_path, monkeypatch)
    # Settle seeding before snapshotting the store (the pool row is seeded from the singleton).
    assert load_pool("xai-oauth").has_available() is True
    before = (home / "auth.json").read_text(encoding="utf-8")

    status = get_xai_oauth_auth_status()

    assert refresh_calls == [], (
        "a read-only xAI status snapshot must not spend the single-use refresh grant; "
        f"attempted {len(refresh_calls)} refresh(es)"
    )
    assert status["logged_in"] is True, status
    assert (home / "auth.json").read_text(encoding="utf-8") == before, (
        "the xAI status snapshot mutated the auth store"
    )


def test_status_snapshot_leaves_a_round_robin_pool_untouched(tmp_path, monkeypatch):
    """Observation is not a lease: no rotation, no accounting, no persist."""
    home, refresh_calls = _hermetic_codex_home(
        tmp_path, monkeypatch, access_tokens=[_jwt_with_exp(3600), _jwt_with_exp(3600)])
    monkeypatch.setattr(credential_pool, "get_pool_strategy", lambda provider: credential_pool.STRATEGY_ROUND_ROBIN)
    assert [e["id"] for e in _persisted_codex_pool(home)] == ["codex-pool-entry-0", "codex-pool-entry-1"]
    before = _persisted_codex_pool(home)

    status = get_codex_auth_status()

    assert refresh_calls == []
    assert status["logged_in"] is True, status
    assert _persisted_codex_pool(home) == before, (
        "a status snapshot rotated or re-counted the persisted pool (routing decision) "
        f"→ {_persisted_codex_pool(home)}"
    )
    assert load_pool("openai-codex").has_available() is True

    # Positive control: a real runtime selection still rotates and persists the new order.
    load_pool("openai-codex").select()
    assert _persisted_codex_pool(home) != before, "a runtime selection must still rotate the pool"


def test_runtime_selection_still_refreshes_by_default(tmp_path, monkeypatch):
    """The read-only observation is opt-in: the runtime path keeps rotating stale tokens."""
    _home, refresh_calls = _hermetic_codex_home(tmp_path, monkeypatch)

    load_pool("openai-codex").select()

    assert len(refresh_calls) == 1
