## What Problem This Solves

Hermes Desktop on Windows resurrects a deleted named (bot) profile within
60–90 seconds of `hermes profile delete <name>` reporting success. Three
cooperating paths cooperate to undo the delete; this PR closes all three so
a deleted profile stays deleted until the user explicitly recreates it.

### Path A — desktop cron multiplex ticker (hermes_cli / cron)

`_start_desktop_cron_ticker` calls `profiles_to_serve()` once at backend
startup and passes a frozen list of profile homes to
`InProcessCronScheduler._start_multiplex`. Every 60 s tick the
`_atomic_write_epoch` inside `record_ticker_heartbeat()` does
`mkdir(parents=True, exist_ok=True)` on `profiles/<name>/cron/`, recreating
the deleted profile's directory shell. Verified: after `rm -rf` of the
resurrected shell, the directory returned exactly one tick later with only
cron files (`ticker_heartbeat`, `ticker_last_success`, `.jobs.lock`,
`.tick.lock`).

### Path B — stale `hermes.desktop.lastProfileByConnection` (renderer)

`apps/desktop/src/store/connections.ts` persists a per-source
last-profile map in `localStorage`. `selectConnection()` reads it on
boot restore and calls `ensureGatewayAgent(<connection>, <deleted
profile>)` with no existence check. The delete dialog only reset this key
in the `if (wasActive)` branch — deleting from another workspace left the
key pointing at the dead profile forever. On the next desktop restart,
`--profile <deleted> serve` is spawned, whose `ensure_hermes_home()`
rebuilds the entire profile tree (`config.yaml`, `state.db`,
`models_dev_cache.json`, logs, SOUL.md reseeded, …).

### Path C — Electron spawn guard's bare `directoryExists`

`spawnPoolBackend()` guards local spawns with
`assertLocalProfileCanStart()` → `directoryExists(profiles/<name>)`. The
cron shell from Path A satisfies the bare check, so the Path B boot
restore lands a real backend spawn, defeating the guard entirely.

## The Fix

### Path A — `cron/scheduler_provider.py`

`_start_multiplex` now checks `Path(home).is_dir()` before every
per-profile heartbeat and tick. A missing home is skipped entirely — no
`mkdir`, no heartbeat, no `cron.tick()` call, no
`set_hermes_home_override`. The frozen `profile_homes` list is left
alone (so the user's profile list still updates on the next gateway
restart); the ticker simply won't act on homes that no longer exist.
Default profile behaviour is unchanged.

### Path B — `apps/desktop/src/store/connections.ts` + delete dialog

New exported helper:

```ts
export function forgetLastProfileForAllConnections(profile: string): void
```

It walks the persisted `$lastProfileByConnection` record and drops every
entry whose normalized value equals the deleted profile name. The
`$lastProfileByConnection` subscriber persists the change to
`localStorage`, so callers don't need to know about the storage layer.

`apps/desktop/src/app/profiles/delete-profile-dialog.tsx` now invokes it
**unconditionally** on confirm (before `deleteProfile()` fires), so
deletes from any workspace — not just the active foreground one —
clean up the cache.

### Path C — `apps/desktop/electron/profile-delete-routing.ts` + `main.ts`

`assertLocalProfileCanStart()` gains an optional fourth argument:

```ts
profileIdentityMarkerPresent: (profile: string) => boolean = () => true
```

When set, the function rejects any profile whose home directory lacks a
durable identity marker. `main.ts` wires this to a new
`hasLocalProfileIdentityMarker()` helper that looks for any of
`config.yaml`, `SOUL.md`, or `state.db` inside `profiles/<name>/`. Any
of these proves the home was written by `hermes profile create` /
`ensure_hermes_home()` / the live runtime — a bare cron shell has
none, so the spawn guard now rejects it with `Profile "<name>" no
longer exists.`. The default profile is exempt from the identity check
(its home may legitimately lack `config.yaml` on fresh installs).

The two existing call sites that use the old three-arg signature keep
working because the new argument defaults to "always present" — no
callers needed changes other than the one in `spawnPoolBackend()`.

## Evidence

### Pre-fix RED → post-fix GREEN

Three regression tests were added against `b742be711a` (origin/main at
PR creation time); each fails on the unfixed tree and passes with the
fix:

| Test | File | What it pins |
|------|------|--------------|
| `test_multiplex_ticker_skips_missing_profile_home_and_does_not_resurrect_it` | `tests/cron/test_scheduler_provider.py` | Path A — ticker must NOT recreate `profiles/<name>/` when it has been deleted between `profiles_to_serve()` and the next tick. Asserts the directory still doesn't exist after several tick cycles and no `cron.tick()` is called for the missing home. |
| `assertLocalProfileCanStart rejects a cron-shell home with no durable identity marker (#95188 path C)` | `apps/desktop/electron/profile-delete-routing.test.ts` | Path C — guard rejects a profile whose home is just a directory shell, even when `directoryExists` returns true. Also confirms the default profile remains exempt from the identity check. |
| `forgetLastProfileForAllConnections removes the deleted profile from every connection (#95188)` | `apps/desktop/src/store/connections.test.ts` | Path B — after seeding two connections' caches with the same deleted profile name, the new helper must purge both entries, and the next `initializeConnectionsRegistry()` boot-restore must dial `'default'`, not the deleted name. |

### Test results

```
$ pytest tests/cron/test_scheduler_provider.py
============================= 31 passed in 4.28s ==============================

$ npx vitest run electron/profile-delete-routing.test.ts src/store/connections.test.ts src/app/profiles/
 Test Files  3 passed (3)
      Tests  45 passed (45)
```

The pre-fix RED evidence (committed in the same branch) is in the commit
history: the new tests fail on `b742be711a` (verified locally with the
fix reverted) and pass once the fix is applied.

### Typecheck clean

```
$ cd apps/desktop && npm run typecheck
(runs `tsc -p . --noEmit && tsc -p tsconfig.electron.json --noEmit && tsc -p tsconfig.e2e.json --noEmit`)
exit 0, no diagnostics.
```

### Pre-existing unrelated failures (not introduced by this PR)

The full vitest run on `b742be711a` already has 31 unrelated failures in
`ssh-config`, `ssh-connection`, `hardening`, `windows-hermes-path`,
`desktop-installation`, `git-worktree-ops`, `update-handoff-marker`, and
`stage-native-deps` tests — all environment-specific (POSIX SSH paths,
chmod-bit semantics, PowerShell hand-off timeouts, …) and present
without my changes. Confirmed by running the suite on a clean
`git stash`'d working tree: same 31 failures. This PR doesn't touch any
of those files.

## Verification Steps

1. Create a bot profile: `hermes --desktop` → Profiles → Bot Mode →
   "researcher".
2. Open the Bots pane and verify it appears; verify
   `~/.hermes/profiles/researcher/{config.yaml,SOUL.md,state.db}` exist.
3. From the `default` workspace (active foreground), open Profiles → ⋯ →
   Delete → confirm for "researcher".
4. Wait 90 seconds (more than one cron tick + heartbeat cycle).
5. Verify `~/.hermes/profiles/researcher/` no longer exists at all.
6. Quit the Desktop app (tray → Quit) and relaunch.
7. Verify "researcher" does not reappear in the profile rail, the Bots
   pane, or `hermes profile list`.
8. Verify `~/.hermes/profiles/researcher/` is still gone.

Without this PR, step 7 shows "researcher" in the profile rail within
~10 s of launch (path B), and steps 4–6 each recreate the cron shell
(path A) and ultimately the full profile (path C).

## Related

- #94842 / #94840 — cron ticker shell / identity-marker predicate (the
  other half of the deletion chain).
- #90141 — tombstone scheme; this PR uses a similar identity-marker
  guard rather than introducing a new tombstone file format.
- #89438 — `sessionSeenCounts`; #95188 calls out that this issue is
  specifically the *uncovered* `lastProfileByConnection` path.
- #94823 — same symptom on macOS.
- #93712 / #93718 — same key, opposite symptom.

Closes #95188
