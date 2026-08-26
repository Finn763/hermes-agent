import type { ClientSessionState } from '../types'

export const DEFAULT_WARM_SESSION_TRANSCRIPT_COUNT = 24
export const DEFAULT_WARM_SESSION_TRANSCRIPT_BYTES = 32 * 1024 * 1024

/**
 * A busy/awaiting entry whose state has not changed for this long is an
 * orphaned mid-turn session (#95276): its turn died without settling, so
 * nothing will ever flip it back to idle and it fails #isWarmSettled forever,
 * pinning its transcript outside both LRU bounds. Chosen far beyond the
 * five-minute session watchdog so legitimate quiet stretches (long tool runs
 * stream no events) never trip it, while still bounding how long a dead turn
 * can hold memory. This window is the SECONDARY guard of the pair: it covers
 * states whose runtime is still published (so #evictOrphans cannot see them)
 * but that no event will ever reach — leftovers reconcile skipped by scope,
 * or turns that died before any reconnect ran.
 */
export const DEFAULT_STALLED_SESSION_MS = 30 * 60 * 1000

interface SessionStateCacheLimits {
  maxBytes?: number
  maxCount?: number
  /** Silence past which a busy/awaiting entry becomes an eviction candidate. */
  stalledMs?: number
  /** Injectable clock for deterministic tests. */
  now?: () => number
}

interface SessionStateCacheCallbacks {
  isReferenced: (runtimeId: string, state: ClientSessionState) => boolean
  /**
   * Whether the runtime id still owns a row in the authoritative
   * `$sessionStates` atom. Reconnect reconcile heals that atom in place, and
   * backend respawns/reclaims retire dead ids' rows outright — but this cache
   * is a second map keyed by runtime id that only live events update, so a
   * busy copy whose id has no row can never receive its terminal settle.
   * Optional: when absent, orphan eviction is disabled.
   */
  isPublishedRuntimeId?: (runtimeId: string) => boolean
  onEvict: (runtimeId: string, state: ClientSessionState) => void
}

function transcriptBytes(state: ClientSessionState): number {
  if (state.messages.length === 0) {
    return 0
  }

  // JS strings occupy two bytes per UTF-16 code unit. JSON also accounts for
  // ids, part tags, tool payloads, attachment metadata, and error text without
  // retaining a second serialized copy in the cache.
  return JSON.stringify(state.messages).length * 2
}

function hasDraftOrInFlightMessage(state: ClientSessionState): boolean {
  return state.messages.some(message => message.pending === true)
}

/**
 * Runtime state map whose settled, unreferenced transcripts form a weighted
 * LRU. Live/visible states and unsaved drafts are outside both limits.
 */
export class SessionStateCache extends Map<string, ClientSessionState> {
  readonly #callbacks: SessionStateCacheCallbacks
  readonly #maxBytes: number
  readonly #maxCount: number
  readonly #stalledMs: number
  readonly #now: () => number
  readonly #recency = new Map<string, number>()
  // Last time each entry's state was replaced via set(). updateSessionState
  // skips writes for unchanged states, so this is "last event activity" —
  // reads must not count, or polling would keep dead sessions fresh forever.
  readonly #mutations = new Map<string, number>()
  #clock = 0

  constructor(callbacks: SessionStateCacheCallbacks, limits: SessionStateCacheLimits = {}) {
    super()
    this.#callbacks = callbacks
    this.#maxBytes = limits.maxBytes ?? DEFAULT_WARM_SESSION_TRANSCRIPT_BYTES
    this.#maxCount = limits.maxCount ?? DEFAULT_WARM_SESSION_TRANSCRIPT_COUNT
    this.#stalledMs = limits.stalledMs ?? DEFAULT_STALLED_SESSION_MS
    this.#now = limits.now ?? (() => Date.now())
  }

  override get(runtimeId: string): ClientSessionState | undefined {
    const state = super.get(runtimeId)

    if (state) {
      this.#touch(runtimeId)
    }

    return state
  }

  override set(runtimeId: string, state: ClientSessionState): this {
    super.set(runtimeId, state)
    this.#touch(runtimeId)
    this.#mutations.set(runtimeId, this.#now())

    return this
  }

  override delete(runtimeId: string): boolean {
    this.#recency.delete(runtimeId)
    this.#mutations.delete(runtimeId)

    return super.delete(runtimeId)
  }

  override clear(): void {
    this.#recency.clear()
    this.#mutations.clear()
    super.clear()
  }

  prune(): void {
    this.#evictOrphans()
    this.#evictStalled()

    const candidates: Array<{ bytes: number; runtimeId: string; state: ClientSessionState; touched: number }> = []
    let bytes = 0

    for (const [runtimeId, state] of this.entries()) {
      if (!this.#isWarmSettled(runtimeId, state)) {
        continue
      }

      const weight = transcriptBytes(state)
      candidates.push({ bytes: weight, runtimeId, state, touched: this.#recency.get(runtimeId) ?? 0 })
      bytes += weight
    }

    let count = candidates.length

    if (count <= this.#maxCount && bytes <= this.#maxBytes) {
      return
    }

    candidates.sort((a, b) => a.touched - b.touched)

    for (const candidate of candidates) {
      if (count <= this.#maxCount && bytes <= this.#maxBytes) {
        break
      }

      // References and activity can change between insertion and pruning.
      const current = super.get(candidate.runtimeId)

      if (current !== candidate.state || !this.#isWarmSettled(candidate.runtimeId, current)) {
        continue
      }

      super.delete(candidate.runtimeId)
      this.#recency.delete(candidate.runtimeId)
      count -= 1
      bytes -= candidate.bytes
      this.#callbacks.onEvict(candidate.runtimeId, candidate.state)
    }
  }

  /**
   * Orphaned mid-turn entries never satisfy #isWarmSettled, so they are
   * invisible to the weighted LRU and would pin their transcripts forever.
   * Unlike that sweep this one is unconditional — it exists to bound memory,
   * not to serve it — but it keeps every protection that makes eviction safe:
   * persisted sessions only, no drafts or in-flight messages, waits that are
   * on the user rather than on a dead turn, and live references win.
   */
  #evictStalled(): void {
    const stalled: Array<{ runtimeId: string; state: ClientSessionState }> = []

    for (const [runtimeId, state] of this.entries()) {
      if (this.#isStalled(runtimeId, state)) {
        stalled.push({ runtimeId, state })
      }
    }

    for (const candidate of stalled) {
      // References and activity can change between detection and eviction.
      const current = super.get(candidate.runtimeId)

      if (current !== candidate.state || !this.#isStalled(candidate.runtimeId, current)) {
        continue
      }

      super.delete(candidate.runtimeId)
      this.#recency.delete(candidate.runtimeId)
      this.#mutations.delete(candidate.runtimeId)
      this.#callbacks.onEvict(candidate.runtimeId, candidate.state)
    }
  }

  /**
   * Deterministic half of the mid-turn eviction pair (#95276): after a
   * reconnect reconcile, `$sessionStates` is the post-reconcile authority. A
   * busy/awaiting copy keyed by a runtime id that no longer appears there
   * belongs to a respawned or reaped runtime — no event can ever reach it, so
   * it fails #isWarmSettled forever and the stall window would be its only
   * bound. Unlike that window this sweep has no threshold to tune and cannot
   * hit a live turn, whose runtime always keeps its atom row by construction
   * (a turn riding out a socket blip re-asserts busy under the same,
   * still-present id). It yields to every standing protection: persisted
   * sessions only, no drafts or in-flight messages, waits that are on the
   * user rather than on a dead turn, and live references win.
   */
  #evictOrphans(): void {
    const orphans: Array<{ runtimeId: string; state: ClientSessionState }> = []

    for (const [runtimeId, state] of this.entries()) {
      if (this.#isOrphaned(runtimeId, state)) {
        orphans.push({ runtimeId, state })
      }
    }

    for (const candidate of orphans) {
      // References and membership can change between detection and eviction.
      const current = super.get(candidate.runtimeId)

      if (current !== candidate.state || !this.#isOrphaned(candidate.runtimeId, current)) {
        continue
      }

      super.delete(candidate.runtimeId)
      this.#recency.delete(candidate.runtimeId)
      this.#mutations.delete(candidate.runtimeId)
      this.#callbacks.onEvict(candidate.runtimeId, candidate.state)
    }
  }

  #isOrphaned(runtimeId: string, state: ClientSessionState): boolean {
    const isPublished = this.#callbacks.isPublishedRuntimeId

    return typeof isPublished === 'function' && this.#isUnreachableMidTurn(runtimeId, state) && !isPublished(runtimeId)
  }

  /** Protection matrix shared by both mid-turn sweeps (#95276): persisted
   *  sessions only, waits that belong to a dead turn rather than to the user
   *  or to an unfinished draft, and never a live reference. */
  #isUnreachableMidTurn(runtimeId: string, state: ClientSessionState): boolean {
    return (
      Boolean(state.storedSessionId) &&
      (state.busy || state.awaitingResponse) &&
      // A blocking prompt is waiting on a human, not on a dead turn.
      !state.needsInput &&
      !hasDraftOrInFlightMessage(state) &&
      !this.#callbacks.isReferenced(runtimeId, state)
    )
  }

  #isStalled(runtimeId: string, state: ClientSessionState): boolean {
    const mutatedAt = this.#mutations.get(runtimeId)

    return (
      this.#isUnreachableMidTurn(runtimeId, state) &&
      mutatedAt !== undefined &&
      this.#now() - mutatedAt >= this.#stalledMs
    )
  }

  #isWarmSettled(runtimeId: string, state: ClientSessionState): boolean {
    return (
      Boolean(state.storedSessionId) &&
      state.messages.length > 0 &&
      !state.busy &&
      !state.awaitingResponse &&
      !state.needsInput &&
      !hasDraftOrInFlightMessage(state) &&
      !this.#callbacks.isReferenced(runtimeId, state)
    )
  }

  #touch(runtimeId: string): void {
    this.#clock += 1
    this.#recency.set(runtimeId, this.#clock)
  }
}
