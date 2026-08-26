import { beforeEach, describe, expect, it } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import type { ChatMessage } from '@/lib/chat-messages'
import { createClientSessionState } from '@/lib/chat-runtime'
import {
  $sessionStates,
  $sessionTiles,
  reconcileBusyStatesOnReconnect,
  releaseSessionTranscript
} from '@/store/session-states'

import { SessionStateCache } from './session-state-cache'

function transcript(id: string, text = id): ChatMessage[] {
  return [
    { id: `${id}-user`, role: 'user', parts: [{ type: 'text', text }] },
    { id: `${id}-assistant`, role: 'assistant', parts: [{ type: 'text', text: `reply ${text}` }] }
  ]
}

function settled(storedSessionId: string, text = storedSessionId): ClientSessionState {
  return { ...createClientSessionState(storedSessionId), messages: transcript(storedSessionId, text) }
}

describe('SessionStateCache', () => {
  beforeEach(() => {
    $sessionStates.set({})
    $sessionTiles.set([])
  })

  it('bounds warm settled transcripts by LRU count and cleans ownership atomically', () => {
    const owners = new Map<string, string>()
    const evicted: string[] = []

    const cache = new SessionStateCache(
      {
        isReferenced: () => false,
        onEvict: (runtimeId, state) => {
          if (state.storedSessionId && owners.get(state.storedSessionId) === runtimeId) {
            owners.delete(state.storedSessionId)
          }

          evicted.push(runtimeId)
        }
      },
      { maxBytes: Number.POSITIVE_INFINITY, maxCount: 2 }
    )

    for (const id of ['a', 'b', 'c']) {
      owners.set(`stored-${id}`, `runtime-${id}`)
      cache.set(`runtime-${id}`, settled(`stored-${id}`))
    }

    // A read makes A warmer than B, so B is the oldest when pruning.
    cache.get('runtime-a')
    cache.prune()

    expect([...cache.keys()].sort()).toEqual(['runtime-a', 'runtime-c'])
    expect(evicted).toEqual(['runtime-b'])
    expect(owners.has('stored-b')).toBe(false)

    // A recycled reverse mapping is not owned by the evicted runtime and must
    // survive cleanup.
    owners.set('stored-a', 'runtime-new-owner')
    cache.set('runtime-d', settled('stored-d'))
    owners.set('stored-d', 'runtime-d')
    cache.prune()
    expect(owners.get('stored-a')).toBe('runtime-new-owner')
  })

  it('uses transcript bytes as well as count', () => {
    const evicted: string[] = []

    const cache = new SessionStateCache(
      { isReferenced: () => false, onEvict: runtimeId => evicted.push(runtimeId) },
      { maxBytes: 600, maxCount: 10 }
    )

    cache.set('small', settled('small', 'x'))
    cache.set('large', settled('large', 'x'.repeat(500)))
    cache.prune()

    expect(evicted).toEqual(['small', 'large'])
    expect(cache.size).toBe(0)
  })

  it.each([
    ['active', (state: ClientSessionState) => state, true],
    ['tiled', (state: ClientSessionState) => state, true],
    ['busy', (state: ClientSessionState) => ({ ...state, busy: true }), false],
    ['awaiting', (state: ClientSessionState) => ({ ...state, awaitingResponse: true }), false],
    ['needs input', (state: ClientSessionState) => ({ ...state, needsInput: true }), false]
  ])('never evicts %s transcripts', (_label, decorate, referenced) => {
    const protectedState = decorate(settled('protected'))

    const cache = new SessionStateCache(
      {
        isReferenced: runtimeId => referenced && runtimeId === 'protected',
        onEvict: () => undefined
      },
      { maxBytes: 0, maxCount: 0 }
    )

    cache.set('protected', protectedState)
    cache.prune()

    expect(cache.get('protected')).toBe(protectedState)
  })

  it('keeps unsaved drafts and pending messages out of the eviction pool', () => {
    const draft = { ...createClientSessionState(null), messages: transcript('draft') }
    const pending = settled('pending')
    pending.messages = [{ id: 'pending-assistant', role: 'assistant', parts: [], pending: true }]

    const cache = new SessionStateCache(
      { isReferenced: () => false, onEvict: () => undefined },
      { maxBytes: 0, maxCount: 0 }
    )

    cache.set('draft', draft)
    cache.set('pending', pending)
    cache.prune()

    expect(cache.has('draft')).toBe(true)
    expect(cache.has('pending')).toBe(true)
  })

  it('retains lightweight status while releasing an evicted transcript', () => {
    const state = { ...settled('stored'), needsInput: false }
    $sessionStates.set({ runtime: state })

    releaseSessionTranscript('runtime')

    expect($sessionStates.get().runtime).toMatchObject({ storedSessionId: 'stored', busy: false, needsInput: false })
    expect($sessionStates.get().runtime.messages).toEqual([])
  })

  describe('stalled session eviction (#95276)', () => {
    function busy(storedSessionId: string, text = storedSessionId): ClientSessionState {
      return { ...settled(storedSessionId, text), busy: true }
    }

    it('evicts entries gone silent past the stall window even under no size pressure', () => {
      let nowMs = 0
      const evicted: string[] = []

      const cache = new SessionStateCache(
        { isReferenced: () => false, onEvict: runtimeId => evicted.push(runtimeId) },
        // No count/byte pressure at all: only the stall guard can bound these.
        {
          maxBytes: Number.POSITIVE_INFINITY,
          maxCount: Number.POSITIVE_INFINITY,
          stalledMs: 10_000,
          now: () => nowMs
        }
      )

      // Mid-turn orphans: committed as busy, then no further events, ever.
      for (const id of ['a', 'b', 'c']) {
        cache.set(`runtime-${id}`, busy(`stored-${id}`, 'x'.repeat(2048)))
      }

      cache.prune()
      expect(cache.size).toBe(3)

      nowMs += 10_001
      cache.prune()

      // Pre-fix this stays 3 forever: busy transcripts are invisible to the LRU.
      expect(cache.size).toBe(0)
      expect([...evicted].sort()).toEqual(['runtime-a', 'runtime-b', 'runtime-c'])
    })

    it.each([
      ['busy', (state: ClientSessionState) => ({ ...state, busy: true }), true],
      ['awaiting response', (state: ClientSessionState) => ({ ...state, awaitingResponse: true }), true],
      ['needs input', (state: ClientSessionState) => ({ ...state, needsInput: true }), false]
    ])('%s past the stall window is evictable exactly when the wait is not on the user', (_label, decorate, stalls) => {
      let nowMs = 0
      const evicted: string[] = []

      const cache = new SessionStateCache(
        { isReferenced: () => false, onEvict: runtimeId => evicted.push(runtimeId) },
        { stalledMs: 5_000, now: () => nowMs }
      )

      cache.set('runtime', decorate(settled('stored')))
      nowMs += 5_001
      cache.prune()

      expect(cache.has('runtime')).toBe(!stalls)
      expect(evicted).toEqual(stalls ? ['runtime'] : [])
    })

    it('keeps a busy transcript alive while events keep arriving and evicts only after silence', () => {
      let nowMs = 0
      const evicted: string[] = []

      const cache = new SessionStateCache(
        { isReferenced: () => false, onEvict: runtimeId => evicted.push(runtimeId) },
        { stalledMs: 10_000, now: () => nowMs }
      )

      cache.set('runtime', busy('stored', 'chunk-0'))

      // A healthy long turn keeps mutating state; each event resets the window.
      for (let tick = 1; tick <= 4; tick += 1) {
        nowMs += 8_000
        cache.set('runtime', busy('stored', `chunk-${tick}`))
        cache.prune()
        expect(cache.has('runtime')).toBe(true)
      }

      // Events stop; silence outlasts the window.
      nowMs += 10_001
      cache.prune()

      expect(cache.has('runtime')).toBe(false)
      expect(evicted).toEqual(['runtime'])
    })

    it('never stall-evicts drafts, in-flight messages, or referenced states', () => {
      let nowMs = 0

      const cache = new SessionStateCache(
        { isReferenced: runtimeId => runtimeId === 'referenced', onEvict: () => undefined },
        { stalledMs: 1, now: () => nowMs }
      )

      const pending = busy('stored-pending')
      pending.messages = [{ id: 'pending-assistant', role: 'assistant', parts: [], pending: true }]
      const draft = { ...createClientSessionState(null), messages: transcript('draft'), busy: true }

      cache.set('pending', pending)
      cache.set('draft', draft)
      cache.set('referenced', busy('stored-referenced'))
      nowMs += 2
      cache.prune()

      expect(cache.has('pending')).toBe(true)
      expect(cache.has('draft')).toBe(true)
      expect(cache.has('referenced')).toBe(true)
    })
  })

  describe('orphaned runtimeId eviction (post-reconcile authority)', () => {
    function busy(storedSessionId: string, text = storedSessionId): ClientSessionState {
      return { ...settled(storedSessionId, text), busy: true }
    }

    it('evicts a busy entry once its runtimeId has no $sessionStates row — no window, no pressure', () => {
      let nowMs = 0
      const evicted: string[] = []

      const cache = new SessionStateCache(
        {
          isReferenced: () => false,
          // Production wiring: membership is read from the authoritative atom.
          isPublishedRuntimeId: runtimeId => runtimeId in $sessionStates.get(),
          onEvict: runtimeId => evicted.push(runtimeId)
        },
        // No count/byte pressure and zero elapsed time: only the membership
        // criterion can drive this eviction.
        {
          maxBytes: Number.POSITIVE_INFINITY,
          maxCount: Number.POSITIVE_INFINITY,
          stalledMs: 10_000,
          now: () => nowMs
        }
      )

      // Two mid-turn sessions committed busy into both layers, as
      // updateSessionState does (cache copy + published atom row).
      for (const id of ['dead', 'live']) {
        const state = busy(`stored-${id}`)

        $sessionStates.set({ ...$sessionStates.get(), [`runtime-${id}`]: state })
        cache.set(`runtime-${id}`, state)
      }

      // Reconnect: reconcile heals the atom's stale flags in place (its
      // documented contract — a live turn re-asserts busy on its next event).
      reconcileBusyStatesOnReconnect()

      // The respawned backend retires the dead runtime id (reclaim/reap);
      // the atom is post-reconcile authority: only live runtimes remain.
      $sessionStates.set({ 'runtime-live': $sessionStates.get()['runtime-live'] })
      // The live turn re-asserts busy under its still-published id.
      cache.set('runtime-live', { ...settled('stored-live'), busy: true })

      cache.prune()

      expect(cache.has('runtime-live')).toBe(true)
      expect(cache.has('runtime-dead')).toBe(false)
      expect(evicted).toEqual(['runtime-dead'])
    })

    it.each([
      [
        'is referenced by a live surface',
        (): ClientSessionState => busy('stored-dead'),
        (runtimeId: string) => runtimeId === 'runtime-dead'
      ],
      ['waits on the user', (): ClientSessionState => ({ ...busy('stored-dead'), needsInput: true }), () => false],
      [
        'holds an in-flight message',
        (): ClientSessionState => {
          const pendingTurn = busy('stored-dead')

          pendingTurn.messages = [{ id: 'pending-assistant', role: 'assistant', parts: [], pending: true }]

          return pendingTurn
        },
        () => false
      ]
    ])('never orphans an unpublished entry that %s', (_label, makeState, isReferenced) => {
      const evicted: string[] = []

      const cache = new SessionStateCache(
        {
          isReferenced,
          isPublishedRuntimeId: () => false,
          onEvict: runtimeId => evicted.push(runtimeId)
        },
        { maxBytes: Number.POSITIVE_INFINITY, maxCount: Number.POSITIVE_INFINITY }
      )

      const state = makeState()

      cache.set('runtime-dead', state)
      cache.prune()

      // The orphan criterion yields to the standing protection matrix.
      expect(cache.get('runtime-dead')).toBe(state)
      expect(evicted).toEqual([])
    })

    it('leaves unsaved drafts alone even when their runtime never published', () => {
      const evicted: string[] = []

      const cache = new SessionStateCache(
        {
          isReferenced: () => false,
          isPublishedRuntimeId: () => false,
          onEvict: runtimeId => evicted.push(runtimeId)
        },
        { maxBytes: Number.POSITIVE_INFINITY, maxCount: Number.POSITIVE_INFINITY }
      )

      const draft = { ...createClientSessionState(null), messages: transcript('draft'), busy: true }

      cache.set('runtime-draft', draft)
      cache.prune()

      // Persisted sessions only: no storedSessionId, no orphan eviction.
      expect(cache.get('runtime-draft')).toBe(draft)
      expect(evicted).toEqual([])
    })

    it('does not touch a live turn riding out a reconnect blip', () => {
      let nowMs = 0
      const evicted: string[] = []

      const cache = new SessionStateCache(
        {
          isReferenced: () => false,
          isPublishedRuntimeId: runtimeId => runtimeId in $sessionStates.get(),
          onEvict: runtimeId => evicted.push(runtimeId)
        },
        { stalledMs: 10_000, now: () => nowMs }
      )

      const liveTurn = busy('stored-live')

      $sessionStates.set({ 'runtime-live': liveTurn })
      cache.set('runtime-live', liveTurn)

      // Reconcile clears busy on the atom row, but the ROW stays published —
      // membership, not flags, decides orphaning, so the blip cannot evict.
      reconcileBusyStatesOnReconnect()
      cache.prune()
      expect(cache.has('runtime-live')).toBe(true)

      // The turn re-asserts busy under the same still-published id.
      nowMs += 500
      cache.set('runtime-live', busy('stored-live'))
      cache.prune()

      expect(cache.has('runtime-live')).toBe(true)
      expect(evicted).toEqual([])
    })

    it('evicts orphans immediately while published-but-silent entries wait out the stall window', () => {
      let nowMs = 0
      const evicted: string[] = []

      const cache = new SessionStateCache(
        {
          isReferenced: () => false,
          isPublishedRuntimeId: runtimeId => runtimeId in $sessionStates.get(),
          onEvict: runtimeId => evicted.push(runtimeId)
        },
        { maxBytes: Number.POSITIVE_INFINITY, maxCount: Number.POSITIVE_INFINITY, stalledMs: 10_000, now: () => nowMs }
      )

      cache.set('runtime-orphan', busy('stored-orphan'))
      cache.set('runtime-quiet', busy('stored-quiet'))
      // The quiet runtime is still published (e.g. reconcile skipped its
      // scope): only the time window can retire it, never the orphan rule.
      $sessionStates.set({ 'runtime-quiet': busy('stored-quiet') })

      cache.prune()
      expect(cache.has('runtime-orphan')).toBe(false)
      expect(cache.has('runtime-quiet')).toBe(true)

      nowMs += 10_001
      cache.prune()

      expect(cache.has('runtime-quiet')).toBe(false)
      expect([...evicted].sort()).toEqual(['runtime-orphan', 'runtime-quiet'])
    })
  })
})
