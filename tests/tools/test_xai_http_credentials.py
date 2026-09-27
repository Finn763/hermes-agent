import pytest


def _set_xai_oauth_unavailable(monkeypatch):
    from hermes_cli import auth
    import hermes_cli.auth_xai as auth_xai

    monkeypatch.setattr(auth, "resolve_xai_oauth_runtime_credentials", lambda **_: {})
    monkeypatch.setattr(auth_xai, "resolve_xai_oauth_runtime_credentials", lambda **_: {})


def test_xai_credentials_fail_closed_without_profile_scope(tmp_path, monkeypatch):
    from agent import secret_scope
    from hermes_cli.config import invalidate_env_cache
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("XAI_API_KEY", "foreign-xai-key")
    monkeypatch.setenv("XAI_BASE_URL", "https://foreign.example/v1")
    _set_xai_oauth_unavailable(monkeypatch)
    invalidate_env_cache()
    previous_multiplex = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope(None)
    secret_scope.set_multiplex_active(True)
    try:
        with pytest.raises(secret_scope.UnscopedSecretError):
            resolve_xai_http_credentials(force_refresh=True)
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous_multiplex)
        invalidate_env_cache()


def test_xai_credentials_do_not_fall_back_to_environ_when_scope_has_no_key(
    tmp_path, monkeypatch
):
    from agent import secret_scope
    from hermes_cli.config import invalidate_env_cache
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("XAI_API_KEY", "foreign-xai-key")
    monkeypatch.setenv("XAI_BASE_URL", "https://foreign.example/v1")
    _set_xai_oauth_unavailable(monkeypatch)
    invalidate_env_cache()
    previous_multiplex = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope({})
    secret_scope.set_multiplex_active(True)
    try:
        credentials = resolve_xai_http_credentials(force_refresh=True)
        assert credentials == {
            "provider": "xai",
            "api_key": "",
            "base_url": "https://api.x.ai/v1",
        }
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous_multiplex)
        invalidate_env_cache()


# ---------------------------------------------------------------------------
# prefer_api_key opt-in (#88040 x_search / #87045 TTS): explicit API key wins
# over subscription OAuth, via the shared resolver — not per-caller inlining.
# ---------------------------------------------------------------------------


def _install_fake_oauth_pool(monkeypatch, oauth_token: str) -> None:
    from types import SimpleNamespace

    entry = SimpleNamespace(
        access_token=oauth_token,
        runtime_api_key=None,
        runtime_base_url=None,
        base_url="https://api.x.ai/v1",
    )

    class _FakePool:
        def select(self):
            return entry

        def try_refresh_matching(self, _hint):
            return entry

    def _fake_load_pool(provider_id):
        if provider_id == "xai-oauth":
            return _FakePool()
        raise KeyError(provider_id)

    monkeypatch.setattr("agent.credential_pool.load_pool", _fake_load_pool)


def test_prefer_api_key_wins_over_available_oauth(monkeypatch):
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "tools.xai_http.get_env_value",
        lambda name, default=None: {"XAI_API_KEY": "paid-key-x1"}.get(name, default),
    )
    _install_fake_oauth_pool(monkeypatch, "oauth-token-x1")

    creds = resolve_xai_http_credentials(prefer_api_key=True)
    assert creds == {
        "provider": "xai",
        "api_key": "paid-key-x1",
        "base_url": "https://api.x.ai/v1",
    }

    # Default order is unchanged: OAuth still wins without the opt-in.
    creds = resolve_xai_http_credentials()
    assert creds["provider"] == "xai-oauth"
    assert creds["api_key"] == "oauth-token-x1"


def test_prefer_api_key_falls_back_to_oauth_without_explicit_key(monkeypatch):
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "tools.xai_http.get_env_value", lambda name, default=None: default
    )
    _install_fake_oauth_pool(monkeypatch, "oauth-token-x1")

    creds = resolve_xai_http_credentials(prefer_api_key=True)
    assert creds["provider"] == "xai-oauth"
    assert creds["api_key"] == "oauth-token-x1"


def test_prefer_api_key_honors_hermes_xai_base_url_with_validation(monkeypatch):
    """The preferred-key path reads the same override pair as the OAuth
    branch (HERMES_XAI_BASE_URL first, then XAI_BASE_URL) behind the same
    origin-pinning validation: an *.x.ai override is honored, a foreign
    origin is rejected in favor of the default."""
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "tools.xai_http.get_env_value",
        lambda name, default=None: {
            "XAI_API_KEY": "paid-key-x1",
            "HERMES_XAI_BASE_URL": "https://staging.x.ai/v1",
            "XAI_BASE_URL": "https://ignored.x.ai/v1",
        }.get(name, default),
    )
    _install_fake_oauth_pool(monkeypatch, "oauth-token-x1")

    creds = resolve_xai_http_credentials(prefer_api_key=True)
    assert creds["base_url"] == "https://staging.x.ai/v1"

    monkeypatch.setattr(
        "tools.xai_http.get_env_value",
        lambda name, default=None: {
            "XAI_API_KEY": "paid-key-x1",
            "XAI_BASE_URL": "https://attacker.example/v1",
        }.get(name, default),
    )
    creds = resolve_xai_http_credentials(prefer_api_key=True)
    assert creds["base_url"] == "https://api.x.ai/v1"


def test_xai_tts_requirements_probe_prefers_api_key(monkeypatch):
    """Sibling site of #87045: tools.tts_tool._xai_requirements() is the
    provider check the text_to_speech_tool dispatch consults. When an explicit
    XAI_API_KEY is configured it must short-circuit on the key — mirroring
    _generate_xai_tts — instead of round-tripping through the OAuth pool for
    an availability probe that the metered /v1/tts endpoint rejects anyway.
    Asserting on pool select/refresh calls keeps the test honest about the
    real defect (truthiness was already True).
    """
    from tools.tts_tool import _xai_requirements

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "tools.xai_http.get_env_value",
        lambda name, default=None: {"XAI_API_KEY": "paid-key-x1"}.get(name, default),
    )
    calls = {"pool_select": 0, "pool_refresh": 0}

    def _recording_load_pool(provider_id):
        from types import SimpleNamespace

        class _RecordingPool:
            def select(self):
                calls["pool_select"] += 1
                return SimpleNamespace(
                    access_token="oauth-token-x1",
                    runtime_api_key=None,
                    runtime_base_url=None,
                    base_url="https://api.x.ai/v1",
                )

            def try_refresh_matching(self, _hint):
                calls["pool_refresh"] += 1
                return None

        if provider_id == "xai-oauth":
            return _RecordingPool()
        raise KeyError(provider_id)

    monkeypatch.setattr("agent.credential_pool.load_pool", _recording_load_pool)

    assert _xai_requirements() is True
    assert calls["pool_select"] == 0, (
        "configured XAI_API_KEY must short-circuit the availability probe; "
        "OAuth pool was consulted for a credential the metered endpoint rejects"
    )
    assert calls["pool_refresh"] == 0, (
        "configured XAI_API_KEY must short-circuit the availability probe; "
        "OAuth pool refresh was triggered"
    )


def test_prefer_api_key_honors_profile_scope_only_key(tmp_path, monkeypatch):
    """A key present only in the active profile's secret scope (not in
    os.environ / .env) is honored on the preferred path — the read goes
    through resolve_provider_secret, not a raw env lookup."""
    from agent import secret_scope
    from hermes_cli.config import invalidate_env_cache
    from tools.xai_http import resolve_xai_http_credentials

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_BASE_URL", raising=False)
    monkeypatch.delenv("HERMES_XAI_BASE_URL", raising=False)
    invalidate_env_cache()
    previous_multiplex = secret_scope.is_multiplex_active()
    token = secret_scope.set_secret_scope({"XAI_API_KEY": "scoped-key-x1"})
    secret_scope.set_multiplex_active(True)
    try:
        creds = resolve_xai_http_credentials(prefer_api_key=True)
        assert creds["provider"] == "xai"
        assert creds["api_key"] == "scoped-key-x1"
        assert creds["base_url"] == "https://api.x.ai/v1"
    finally:
        secret_scope.reset_secret_scope(token)
        secret_scope.set_multiplex_active(previous_multiplex)
        invalidate_env_cache()
