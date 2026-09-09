## Problem

In Desktop Bot Mode the Routines/Cronjobs pane renders the hardcoded placeholder

> Cronjobs are unavailable until this agent appears in the roster.

for **every** bot whenever the SDK exposes `focusedSessionOwner` but the currently focused session has no bot owner — a normal (non-Bot-Chat) session, or ambiguous owner hints, both of which the SDK intentionally resolves to `null`. `resolveRoutineOwner` treated that `null` as an error and returned `null` before ever consulting the roster selection, dead-ending the pane even though the roster is populated and the user just clicked a bot. Regression reported on v0.20.5 Windows 11 (local backend) and confirmed on a remote Linux backend.

Closes #94516 (duplicate of #94483; the roster-hydration half of that report is addressed separately by #94549 — see Exclusions).

## Root cause

`apps/desktop/src/plugins/hermes-bots/plugin.js`:

- `hasFocusedSessionOwnerSupport = Boolean(host.state.focusedSessionOwner)` was latched at plugin register.
- When the store is present, `resolveRoutineOwner` fail-closed on `!focusedOwner` → `null`, so `RoutinesPane` rendered the unavailable placeholder for every agent.

The SDK's `$focusedSessionOwner` is `null` exactly in the ordinary browsing state (focused session = normal chat, or ambiguous owner hints — `apps/desktop/src/sdk/index.ts`). The fail-closed intent (never route cron reads/mutations through a stale selection when an *authoritative* owner exists) is preserved; the removed gate only handled the *null* owner case, which is not an error at all.

## Fix

Drop the fail-closed null gate in `resolveRoutineOwner` and fall through to the existing selection ladder (`focusedBot || selectedBot || …`): with no focused owner the pane scopes to the bot the user clicked in the roster — the previously working behavior. The safety contract is unchanged:

- an authoritative focused owner still wins through its exact roster row and still fails closed when that row is absent;
- a null owner with no matching selection also still fails closed.

Renderer-only, platform-neutral change; no API, state-schema, or backend cron-store changes.

## Tests

- Replaced the unit test that encoded the buggy gate with two #94516 regressions in `apps/desktop/src/plugins/hermes-bots/tests/routines-selected-bot.test.mjs`:
  1. null focused owner + clicked bot present in roster → resolves to the clicked bot;
  2. null focused owner + selection with no roster row → still fails closed.
- Red/green proven: the regression fails on pre-fix code (`resolveRoutineOwner` returned `null`) and passes with the fix.
- Full plugin suite `node --test src/plugins/*/tests/*.test.mjs`: **557/557 pass**.
- `npx vitest run --project electron --project ui`: `ui` project passes; `electron` project has 32 failures that are pre-existing Windows-environment baselines in unrelated files (chmod-mode assertions, `ADMINI~1` short-path casing, POSIX ssh socket paths, EPERM temp cleanup) — none in the hermes-bots plugin or the SDK, none touched by this diff.
- `npm run typecheck` (all three tsconfig projects): clean.

## Risk / Exclusions

- The RoutinesPane's non-reactive roster read (`$lastRoster.get()` → `useValue($lastRoster)`) is fixed by #94549; this PR intentionally leaves that call site untouched to avoid conflicting with that PR. The two fixes are complementary: #94549 keeps the pane re-rendering when the roster hydrates late, this one fixes owner resolution for the already-hydrated roster.
- No drive-by changes; the diff is limited to `resolveRoutineOwner` (+ its now-unused support constant) and the test file.
