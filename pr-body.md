## Root cause

`computer_use.grant_existing_profile` is enforced by two different reads that drift apart mid-session:

1. **Host-side floor** — `CuaTypedBrowserRoute.prepare` reads `_cua_grant_existing_profile()` on every call (fresh `load_config()`), so `hermes config set computer_use.grant_existing_profile true` immediately lets the request past the floor.
2. **Driver launch grant** — the same key is baked into the cua-driver launch args once, at backend bind time (`--grant existing-profile` in `_standard_runtime_launch_args`), and the backend is cached per Hermes session.

When the user enables the grant mid-session, path 1 passes but the running driver was launched without the grant, so its daemon authorization coordinator keeps refusing with `browser_consent_required` (state bind) or verify-ladder refusals naming the Chrome remote-debugging consent sheet (prepare). Nothing in that loop ever says that a config write that "succeeds" cannot reach the running driver — the only fix is rebinding the session. A third path, `browser_exec`/`browser-harness mac-approve` guidance, passes through driver text that names a helper which is not on the user's PATH.

(The third issue item — `hermes computer-use browser-approve --help` advertising the removed upstream token path — no longer reproduces on main: the command was removed in a403fe6f9 and tests assert its absence.)

## Fix

One truthful, tool-enforced contract across both paths:

- `CuaDriverBackend` no longer snapshots the grant at bind time. The snapshot lives where the launch args are baked: `_CuaDriverSession.start()` re-reads `computer_use.grant_existing_profile` on **every** launch — the lazy first start and each post-transport-reset relaunch — and records it as `_grant_existing_profile_at_launch`; `_lifecycle_coro` bakes `--grant existing-profile` from that same value, so the recorded grant and the launch args can never diverge.
- `typed_browser_state` / `typed_browser_prepare` post-process driver refusals: when a consent-family refusal arrives (code `browser_consent_required`, any code containing `consent`, or a message naming the Chrome remote-debugging consent sheet) **and** the config now grants existing-profile **but** the runtime in flight demonstrably launched without the grant, the refusal is rewritten to a single actionable blocker `browser_existing_profile_grant_stale`: "start a new Hermes session (or restart the agent)" — with the original driver refusal preserved under `driver_refusal`. Before any launch has happened (lazy-start window) the rewrite is skipped: the upcoming launch re-reads the config and picks the grant up, so a consent refusal there is the legitimate consent flow, never "restart the session".
- The rewrite is scoped to `existing_profile` prepares only; isolated launches are never rewritten.
- The host-side floor refusal (`browser_existing_profile_not_granted`) now states the immutability contract up front: a mid-run `config set` binds only when the session restarts.
- Driver guidance that names `browser-harness` is annotated: the helper ships inside the cua-driver install, not as a bare PATH command, and `hermes computer-use status` locates the driver binary.
- Docs (`website/docs/user-guide/features/computer-use.md`) document the launch-setting contract and the `browser_existing_profile_grant_stale` outcome.

## Evidence

Pre-fix (RED), the new behavioral regression tests fail against unmodified code; post-fix (GREEN):

- `tests/tools/test_computer_use_browser_authorization.py` (+12 tests, 53 total) — mid-session grant enablement reports the rebind action on both `cua_browser_state` and `cua_browser_prepare`; consent refusals pass through untouched when the grant was bound at launch / is still off / is an isolated launch / is a non-consent refusal; the floor refusal names the rebind action; `browser-harness` guidance is annotated with its location; **plus the adversarial-review windows**: the launch snapshot re-reads config at every `start()` (lazy first start and post-reset relaunch), a refusal before any launch passes through instead of being rewritten, and both end-to-end windows (grant enabled before first launch, grant enabled across a transport reset) pass the consent refusal through because the launched runtime demonstrably has the grant.
- Full computer-use suites: 115 passed (`test_computer_use_browser_authorization`, `test_computer_use_cua_0_9`, `test_computer_use_cua_0_10_permissions`, `test_computer_use_browser_contract_020`); 300 passed / 2 skipped / 2 pre-existing environment failures in the wider `tests/tools/test_computer_use*` sweep (both fail identically on clean `0159b51f2b` on this Windows host); `tests/computer_use/` 39 passed / 4 skipped / 2 pre-existing WSL-path environment failures (also identical on clean base).

## Scope caveat

The macOS E2E acceptance criterion (existing Chrome → grant → exact consent sheet → authenticated read) cannot be executed from this Windows dev host; this PR fixes everything static and provable regardless of platform, per the claim comment on the issue. The `browser_exec` isolated-vs-existing default overlap is left to #89143.

Closes #93068
