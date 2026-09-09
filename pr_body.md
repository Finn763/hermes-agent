## What Problem This Solves

Closes #96328.

Hermes's macOS `computer_use` private-session driver gate rejected two install shapes that ship with cua-driver 0.22.x:

1. **Symlink shim at the standard installer location.** The official installer drops `~/.local/bin/cua-driver` as a symlink into the carrying `CuaDriver.app`. `_resolve_cua_driver_app_path` parsed the unresolved shim path, so its `.app/Contents/MacOS/` marker scan missed the bundle. The function returned `None` and the daemon launch raised `CuaDriver.app is required for private computer-use sessions on macOS`.

2. **Current Cua AI, Inc. team ID was not on the allowlist.** cua-driver 0.22.x is notarised by Cua AI, Inc. (`TeamIdentifier=YCK386LBJ7`), not the original developer Apple ID (`4YEC26S9KF`). The legacy scalar `_CUA_DRIVER_TEAM_ID` could only hold one value, so the validator raised `CuaDriver.app ... is signed by team 'YCK386LBJ7', expected '4YEC26S9KF'`.

Both bugs caused the same user-facing symptom: a fresh `hermes computer-use install` followed by any private session on macOS fails closed with "signed by team" or "CuaDriver.app is required" even though the bundle is the genuine, notarised upstream driver.

## Evidence

Two targeted code changes and a defensive path-separator normalization so the function is correct on every host (macOS is unchanged at runtime; the normalization matters for cross-platform tests):

- `tools/computer_use/cua_backend.py::_resolve_cua_driver_app_path` runs `os.path.realpath` first so the shim resolves to the binary inside the carrying `CuaDriver.app` before the marker scan. Path separators are normalized to forward slashes for the marker scan only; the returned candidate is converted back to OS-native separators so downstream `os.path.join` + `isfile` probes and the caller's invocation both work on the host that produced the path.
- `tools/computer_use/cua_backend.py` replaces the scalar `_CUA_DRIVER_TEAM_ID = "4YEC26S9KF"` with `_CUA_DRIVER_TRUSTED_TEAM_IDS = frozenset({"4YEC26S9KF", "YCK386LBJ7"})`. The exact bundle-id check (`com.trycua.driver`) is unchanged so a suffixed impostor still fails closed. `allow_unsigned_driver` opt-in is preserved.
- `hermes_cli/config_defaults.py` updates the `allow_unsigned_driver` docstring to document both trusted team IDs.

Regression tests in `tests/tools/test_computer_use_driver_signature.py` (17 tests, all pass post-fix on Windows runner with the host-portable path-separator handling; behavior on macOS is unchanged):

- `TestResolveCuaDriverAppPath` (4 tests) — symlink shim resolves to the bundle, pre-resolved absolute path is idempotent, paths outside any `.app` return `None`, shims pointing outside an `.app` return `None`.
- `TestValidateCuaDriverAppSignature` (10 tests) — frozenset allowlist contains both trusted IDs, bundle-id constant unchanged, accepts current Cua AI team ID, accepts legacy team ID, rejects unknown team ID, rejects suffixed identifier even with a trusted team, unsigned rejected by default, unsigned allowed by `allow_unsigned_driver: true`, unsigned with wrong identifier still rejected, codesign unavailable raises runtime error, unsigned bundle rejected.
- `TestEmbeddedDaemonSpawnWithSymlinkAndCurrentSigning` (1 test) — end-to-end reproduction of #96328: `~/.local/bin/cua-driver` symlink into a CuaDriver.app signed by `YCK386LBJ7` resolves, validates, and builds the `open -n -g -a CuaDriver.app --args ...` launch command.
- Module-level `test_non_macos_embedded_daemon_skips_signature_gate` — non-darwin fast-path bypasses the macOS-only signature gate.

Test results (`D:/code/venvs/hermes-dev/Scripts/python.exe -m pytest tests/tools/test_computer_use_driver_signature.py -v`):

```
collected 17 items
tests/tools/test_computer_use_driver_signature.py::TestResolveCuaDriverAppPath::test_resolves_when_driver_path_is_a_symlink_into_app PASSED
tests/tools/test_computer_use_driver_signature.py::TestResolveCuaDriverAppPath::test_resolves_when_driver_path_is_already_resolved PASSED
tests/tools/test_computer_use_driver_signature.py::TestResolveCuaDriverAppPath::test_returns_none_for_path_outside_any_app_bundle PASSED
tests/tools/test_computer_use_driver_signature.py::TestResolveCuaDriverAppPath::test_returns_none_when_shim_does_not_point_into_an_app PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_trusted_team_allowlist_contains_legacy_and_current PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_bundle_id_constant_unchanged PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_accepts_current_cua_ai_team_id PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_accepts_legacy_team_id PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_rejects_unknown_team_id PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_rejects_suffixed_identifier_even_with_trusted_team PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_unsigned_rejected_by_default PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_unsigned_allowed_by_config_opt_in PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_unsigned_with_wrong_identifier_still_rejected PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_codesign_unavailable_raises_runtime_error PASSED
tests/tools/test_computer_use_driver_signature.py::TestValidateCuaDriverAppSignature::test_unsigned_bundle_rejected PASSED
tests/tools/test_computer_use_driver_signature.py::TestEmbeddedDaemonSpawnWithSymlinkAndCurrentSigning::test_spawn_command_built_from_shim_with_current_team PASSED
tests/tools/test_computer_use_driver_signature.py::test_non_macos_embedded_daemon_skips_signature_gate PASSED

17 passed in 3.51s
```

Pre-fix verification: with the source modifications stashed, the test for `_CUA_DRIVER_TRUSTED_TEAM_IDS` cannot be collected because the constant does not exist (pre-fix `_CUA_DRIVER_TEAM_ID` is a scalar string), and the symlink-shim resolver test fails because the function returns `None` instead of the bundle path. Post-fix all 17 pass.

Related computer_use test files (`tests/tools/test_computer_use.py`, `tests/tools/test_computer_use_cua_0_10_permissions.py`, `tests/tools/test_computer_use_empty_discovery_diagnosis.py`) — 120 pass, 1 unrelated pre-existing failure in `test_cli_fallback_reads_screenshot_from_file` (verified to fail identically on `main` without these changes; it exercises the CLI fallback for `get_window_state`, which does not touch `_resolve_cua_driver_app_path` or the signature validator).