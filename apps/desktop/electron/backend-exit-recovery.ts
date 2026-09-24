/**
 * Supervisor decision for a primary backend child's post-ready exit (#112344).
 *
 * The child's `exit` handler classifies the exit as "current" (this child
 * still owned the connection slot) or "stale" (the slot was already cleared
 * or moved on). A stale exit is normally harmless: a replacement owns the
 * slot or a start is already in flight. But the same classification also
 * fires when the slot was emptied and NOTHING followed — the connection was
 * invalidated without a replacement start, or the child's own `error`
 * handler cleared the slot first — and then the UI keeps running with no
 * engine until the user relaunches the app (9 h observed).
 *
 * `claim` answers "does the supervisor own the respawn for this exit?" from
 * the primary slot's state alone. Pool children never enter the decision:
 * they do not own the window backend and must not suppress its recovery.
 */
export type BackendExitRecoveryState = {
  /** A live primary (local child or remote descriptor) or a published attempt still holds the slot. */
  hasCurrentOwner: boolean
  /** startHermes() is running but has not published its attempt yet. */
  hasPendingStart: boolean
  /** The slot was emptied on purpose (re-home, quit, hand-off, latched boot failure). */
  intentionalTeardown: boolean
}

export type BackendExitRecoveryOptions = {
  /** Respawns the supervisor grants per `windowMs` before it stops and lets the user relaunch. */
  maxRespawns?: number
  windowMs?: number
  now?: () => number
}

export type PrimaryExitRecoveryOutcome = 'respawn' | 'crash-loop' | 'ignore'

/**
 * Drive the latch for one primary-child termination and decide the supervisor's move:
 *
 * - `'respawn'` — the supervisor owns a (re-)grant and must spawn;
 * - `'crash-loop'` — the bounded window is spent, surface the explicit state;
 * - `'ignore'` — no claim is owned (user-driven start, live owner, pending start,
 *   intentional teardown): recovery stays with the caller/boot-error path.
 *
 * A granted respawn that dies BEFORE ready used to leave the latch `claimed`
 * forever: `reset()` only runs on the ready transition, so the pre-ready exit
 * was refused before the claim was even consulted and every later exit was
 * dropped in silence — no respawn, no log, no engine until the app was relaunched
 * (#118680, two overnight occurrences). The pre-ready branch now re-arms through
 * `retryAfterFailedStart`, which spends the same bounded crash-loop budget as any
 * other supervisor retry, so exhaustion surfaces as `'crash-loop'` instead of
 * silence.
 */
export function drivePrimaryExitRecovery(
  latch: ReturnType<typeof createBackendExitRecoveryLatch>,
  ready: boolean,
  state: BackendExitRecoveryState
): PrimaryExitRecoveryOutcome {
  const granted = ready ? latch.claim(state) : latch.retryAfterFailedStart(state)

  if (granted) {
    return 'respawn'
  }

  return latch.isCrashLooping() ? 'crash-loop' : 'ignore'
}

export function createBackendExitRecoveryLatch({
  maxRespawns = 3,
  windowMs = 120_000,
  now = Date.now
}: BackendExitRecoveryOptions = {}) {
  let claimed = false
  let respawnedAt: number[] = []
  let crashLooping = false

  const blocked = (state: BackendExitRecoveryState) =>
    state.hasCurrentOwner || state.hasPendingStart || state.intentionalTeardown

  const claim = (state: BackendExitRecoveryState): boolean => {
    if (claimed || blocked(state)) {
      return false
    }

    const at = now()
    respawnedAt = respawnedAt.filter(t => at - t < windowMs)
    crashLooping = respawnedAt.length >= maxRespawns

    if (crashLooping) {
      return false
    }

    respawnedAt.push(at)
    claimed = true

    return true
  }

  return {
    /**
     * True exactly once per empty slot; `reset()` when a backend becomes ready
     * again. A backend that dies again shortly after every ready re-arms the
     * latch each time, so the grant is also bounded: more than `maxRespawns`
     * within `windowMs` is a crash loop, and the supervisor stops respawning
     * (`isCrashLooping()`) instead of cycling child + error toast forever.
     */
    claim,
    /**
     * Re-arm only the recovery attempt that already owns this latch and failed
     * before reaching ready. A concurrent owner/start or intentional teardown
     * keeps the existing claim intact; its eventual ready transition owns the
     * normal reset. A real retry consumes the same crash-loop budget as every
     * other supervisor respawn.
     */
    retryAfterFailedStart(state: BackendExitRecoveryState): boolean {
      if (!claimed || blocked(state)) {
        return false
      }

      claimed = false

      return claim(state)
    },
    /** True when the last claim attempt was refused because the respawn budget for the window is spent. */
    isCrashLooping(): boolean {
      return crashLooping
    },
    reset(): void {
      claimed = false
    }
  }
}
