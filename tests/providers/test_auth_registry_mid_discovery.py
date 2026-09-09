"""Regression test for #102123.

``hermes_cli.auth.PROVIDER_REGISTRY`` used to be a one-shot snapshot taken at
import time. When ``hermes_cli.auth`` was first imported mid-discovery (e.g. by
an entry-point plugin), the snapshot only saw the providers registered up to
that moment and stayed incomplete forever — late providers were present in
``providers._REGISTRY`` but absent from ``PROVIDER_REGISTRY``.
"""
from __future__ import annotations

import sys

import pytest

import providers
import hermes_cli.auth as auth_mod
from providers.base import ProviderProfile


EARLY = "probe-102123-early"
LATE = "probe-102123-late"
LATE_ALIAS = "probe-102123-late-alias"


@pytest.fixture()
def _isolated_registries():
    """Snapshot both registries; restore on teardown so nothing leaks."""
    saved_registry = dict(providers._REGISTRY)
    saved_aliases = dict(providers._ALIASES)
    saved_discovered = providers._discovered
    saved_auth_keys = set(auth_mod.PROVIDER_REGISTRY)
    saved_plugin_modules = {
        m for m in sys.modules if m.startswith("plugins.model_providers")
    }
    providers._REGISTRY.clear()
    providers._ALIASES.clear()
    providers._PROVIDER_LIST_CACHE = None
    providers._discovered = False
    yield
    for key in set(auth_mod.PROVIDER_REGISTRY) - saved_auth_keys:
        del auth_mod.PROVIDER_REGISTRY[key]
    for mod in [
        m
        for m in sys.modules
        if m.startswith("plugins.model_providers")
        and m not in saved_plugin_modules
    ]:
        del sys.modules[mod]
    providers._REGISTRY.clear()
    providers._REGISTRY.update(saved_registry)
    providers._ALIASES.clear()
    providers._ALIASES.update(saved_aliases)
    providers._PROVIDER_LIST_CACHE = None
    providers._discovered = saved_discovered


def test_mid_discovery_auth_import_reconciled(
    _isolated_registries, monkeypatch, tmp_path
):
    """Mid-discovery auth snapshot + late provider -> REGISTRY finally complete."""

    def fake_entry_point_step():
        # Reproduce a mid-discovery `import hermes_cli.auth`: its module
        # top-level snapshots list_providers() exactly like this. Only EARLY
        # is registered at this point on the way in (actually nothing yet —
        # EARLY registers just below), so the snapshot is partial by design.
        for pp in providers.list_providers():
            if pp.name not in auth_mod.PROVIDER_REGISTRY:
                auth_mod._register_plugin_provider(pp)
        providers.register_provider(
            ProviderProfile(
                name=EARLY,
                display_name="Early",
                base_url="https://early.example/v1",
                env_vars=("PROBE_102123_EARLY_KEY",),
            )
        )
        # The snapshot above could only see EARLY-or-nothing: prove the hazard
        # setup is real before discovery continues.
        assert LATE not in auth_mod.PROVIDER_REGISTRY

    late_plugin = tmp_path / "zzz_probe_102123"
    late_plugin.mkdir()
    (late_plugin / "__init__.py").write_text(
        "from providers import register_provider\n"
        "from providers.base import ProviderProfile\n"
        "register_provider(ProviderProfile(\n"
        f"    name={LATE!r}, display_name='Late',\n"
        "    base_url='https://late.example/v1',\n"
        f"    env_vars=('PROBE_102123_LATE_KEY',), aliases=({LATE_ALIAS!r},)))\n",
        encoding="utf-8",
    )
    sys.modules.pop("plugins.model_providers.zzz_probe_102123", None)
    monkeypatch.setattr(
        providers, "_discover_entry_point_providers", fake_entry_point_step
    )
    monkeypatch.setattr(providers, "_BUNDLED_PLUGINS_DIR", tmp_path)
    monkeypatch.setattr(providers, "_user_plugins_dir", lambda: None)
    monkeypatch.setattr(providers, "_installed_plugins_dir", lambda: None)

    providers._discover_providers()

    assert providers.get_provider_profile(LATE) is not None
    assert LATE in auth_mod.PROVIDER_REGISTRY
    assert LATE_ALIAS in auth_mod.PROVIDER_REGISTRY
    assert EARLY in auth_mod.PROVIDER_REGISTRY


def test_sync_idempotent_and_no_clobber(_isolated_registries):
    """Repeated syncs add nothing new and never replace existing entries."""
    snapshot = dict(auth_mod.PROVIDER_REGISTRY)
    auth_mod.sync_plugin_providers_to_registry()
    for key, config in snapshot.items():
        assert auth_mod.PROVIDER_REGISTRY[key] is config
    after = dict(auth_mod.PROVIDER_REGISTRY)
    assert auth_mod.sync_plugin_providers_to_registry() == 0
    assert dict(auth_mod.PROVIDER_REGISTRY) == after


def test_sync_noop_when_auth_never_imported(monkeypatch):
    """The providers-side hook must not import hermes_cli.auth by itself."""
    monkeypatch.delitem(sys.modules, "hermes_cli.auth", raising=False)
    providers._sync_auth_registry()
    assert "hermes_cli.auth" not in sys.modules
