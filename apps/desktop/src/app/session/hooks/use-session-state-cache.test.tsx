import { act, cleanup, render } from '@testing-library/react'
import { type MutableRefObject, useLayoutEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChatMessage } from '@/lib/chat-messages'
import {
  $activeSessionStoredIdRotation,
  $currentFastMode,
  $currentModel,
  $currentProvider,
  $currentReasoningEffort,
  $currentServiceTier,
  $messages,
  $turnStartedAt,
  setActiveSessionId,
  setActiveSessionStoredIdRotation,
  setCurrentFastMode,
  setCurrentModel,
  setCurrentProvider,
  setCurrentReasoningEffort,
  setCurrentServiceTier,
  setTurnStartedAt
} from '@/store/session'
import {
  $sessionStates,
  clearAllSessionStates,
  reconcileBusyStatesOnReconnect,
  type SessionTileDelegate,
  setSessionTileDelegate
} from '@/store/session-states'

import { useSessionStateCache } from './use-session-state-cache'

type Cache = ReturnType<typeof useSessionStateCache>

interface HarnessProps {
  activeSessionId: string | null
  onReady: (cache: Cache) => void
  selectedStoredSessionId: string | null
}

describe('useSessionStateCache — stored-id rotation provenance', () => {
  afterEach(() => {
    cleanup()
    setActiveSessionId(null)
    setActiveSessionStoredIdRotation(null)
  })

  it('emits the previous, next, and runtime ids and removes the stale reverse mapping', () => {
    let cache!: Cache

    setActiveSessionId('runtime-A')
    render(
      <Harness activeSessionId="runtime-A" onReady={value => (cache = value)} selectedStoredSessionId="stored-A" />
    )

    act(() => {
      cache.updateSessionState('runtime-A', state => state, 'stored-A')
      cache.updateSessionState('runtime-A', state => state, 'stored-A-next')
    })

    expect($activeSessionStoredIdRotation.get()).toEqual({
      nextStoredSessionId: 'stored-A-next',
      previousStoredSessionId: 'stored-A',
      runtimeSessionId: 'runtime-A'
    })
    expect(cache.runtimeIdByStoredSessionIdRef.current.has('stored-A')).toBe(false)
    expect(cache.runtimeIdByStoredSessionIdRef.current.get('stored-A-next')).toBe('runtime-A')
  })

  it('does not publish a foreground-navigation event for a background runtime rotation', () => {
    let cache!: Cache

    setActiveSessionId('runtime-B')
    render(
      <Harness activeSessionId="runtime-B" onReady={value => (cache = value)} selectedStoredSessionId="stored-B" />
    )

    act(() => {
      cache.updateSessionState('runtime-A', state => state, 'stored-A')
      cache.updateSessionState('runtime-A', state => state, 'stored-A-next')
    })

    expect($activeSessionStoredIdRotation.get()).toBeNull()
    expect(cache.runtimeIdByStoredSessionIdRef.current.has('stored-A')).toBe(false)
    expect(cache.runtimeIdByStoredSessionIdRef.current.get('stored-A-next')).toBe('runtime-A')
  })
})

describe('useSessionStateCache — a proven stored↔runtime binding is never stolen by a stale pair', () => {
  afterEach(() => {
    cleanup()
    clearAllSessionStates()
    setActiveSessionId(null)
    setActiveSessionStoredIdRotation(null)
  })

  // THE PICTURE ON SCREEN: the OLD conversation (stored-old) and the
  // conversation the user has since opened (stored-new) each own a live
  // runtime. A stale send-time pair — a pipeline that still holds the OLD
  // runtime id while its target stored id is the NEW session's, the shape
  // submit.ts documents for a queue drain ("sessionId=A-runtime with
  // storedSessionId=B") — arrives at the one shared write path. Accepting it
  // RELABELS the old runtime onto the new session, so the next Enter in the
  // new session resolves a "proven" runtime that is really the old one's and
  // the turn persists into the old conversation while the UI shows the new.
  it('① a stale pair cannot relabel a live runtime onto a session another runtime owns', () => {
    let cache!: Cache

    setActiveSessionId('rt-old')
    render(<Harness activeSessionId="rt-old" onReady={value => (cache = value)} selectedStoredSessionId="stored-old" />)

    act(() => {
      cache.updateSessionState('rt-other', state => ({ ...state, model: 'other' }), 'stored-new')
      cache.updateSessionState('rt-old', state => ({ ...state, model: 'old' }), 'stored-old')
    })

    act(() => {
      cache.updateSessionState('rt-old', state => ({ ...state, model: 'stolen' }), 'stored-new')
    })

    // The new session's next turn must still resolve to the runtime that owns
    // it — not to the old conversation's live runtime.
    expect(cache.runtimeIdByStoredSessionIdRef.current.get('stored-new')).toBe('rt-other')
    expect(cache.getRuntimeIdForStoredSession('stored-new')).toBe('rt-other')
    // The refusal must stop the caller's own update too, not just the binding:
    // that updater is written for the session it MEANT (stored-new), so landing
    // it here would publish the other conversation's state under this runtime —
    // a stale seedOptimistic appends its prompt and marks it busy. `old`, not
    // `stolen`, is what proves the update was dropped.
    expect($sessionStates.get()['rt-old']?.model).toBe('old')
    // The old runtime keeps its own durable identity.
    expect($sessionStates.get()['rt-old']?.storedSessionId).toBe('stored-old')
    expect(cache.getRuntimeIdForStoredSession('stored-old')).toBe('rt-old')
    // And no foreground navigation event is published to route the view onto
    // the other conversation off the back of a stale pair.
    expect($activeSessionStoredIdRotation.get()).toBeNull()
  })

  // PROTECTION: the rotation the mechanism exists for — one conversation's own
  // stored-id rotation (auto-compression mints a new tip) — still lands.
  it('② a genuine stored-id rotation of the same runtime still applies', () => {
    let cache!: Cache

    setActiveSessionId('rt-a')
    render(<Harness activeSessionId="rt-a" onReady={value => (cache = value)} selectedStoredSessionId="stored-a" />)

    act(() => {
      cache.updateSessionState('rt-a', state => ({ ...state, model: 'a' }), 'stored-a')
      cache.updateSessionState('rt-a', state => ({ ...state, model: 'tip' }), 'stored-a-tip')
    })

    expect($activeSessionStoredIdRotation.get()).toEqual({
      nextStoredSessionId: 'stored-a-tip',
      previousStoredSessionId: 'stored-a',
      runtimeSessionId: 'rt-a'
    })
    expect(cache.runtimeIdByStoredSessionIdRef.current.has('stored-a')).toBe(false)
    expect(cache.runtimeIdByStoredSessionIdRef.current.get('stored-a-tip')).toBe('rt-a')
    expect($sessionStates.get()['rt-a']?.storedSessionId).toBe('stored-a-tip')
  })

  // PROTECTION: a fast A → B → A switch writes each session's own turn through
  // the same entry. Nothing is lost and nothing crosses over.
  it('③ a fast switch keeps each session on its own runtime and its own messages', () => {
    let cache!: Cache

    const userMessage = (id: string): ChatMessage => ({ id, parts: [{ text: id, type: 'text' }], role: 'user' })

    setActiveSessionId('rt-a')
    render(<Harness activeSessionId="rt-a" onReady={value => (cache = value)} selectedStoredSessionId="stored-a" />)

    act(() => {
      cache.updateSessionState('rt-a', state => ({ ...state, messages: [userMessage('in-a')] }), 'stored-a')
      cache.updateSessionState('rt-b', state => ({ ...state, messages: [userMessage('in-b')] }), 'stored-b')
    })

    // ...switch back to A and send again.
    act(() => {
      cache.updateSessionState('rt-a', state => ({
        ...state,
        messages: [...state.messages, userMessage('in-a-2')]
      }))
    })

    expect($sessionStates.get()['rt-a']?.messages.map(message => message.id)).toEqual(['in-a', 'in-a-2'])
    expect($sessionStates.get()['rt-b']?.messages.map(message => message.id)).toEqual(['in-b'])
    expect(cache.runtimeIdByStoredSessionIdRef.current.get('stored-a')).toBe('rt-a')
    expect(cache.runtimeIdByStoredSessionIdRef.current.get('stored-b')).toBe('rt-b')
    expect($activeSessionStoredIdRotation.get()).toBeNull()
  })

  // The other half of the same ownership decision, on a runtime with no cache
  // entry yet: the `session.info` a rebuilt runtime (a mid-conversation
  // model/provider switch) emits for the conversation already on screen while
  // the runtime that works it is still there. maybeRebindPaneToRebuiltRuntime
  // refuses to adopt that rebuilt runtime for exactly this reason, so the
  // binding must not move to it either — otherwise the conversation's next send
  // resolves onto a runtime the pane is not following.
  it('④ a freshly observed runtime cannot take a stored id from a live holder', () => {
    let cache!: Cache

    setActiveSessionId('rt-owner')
    render(<Harness activeSessionId="rt-owner" onReady={value => (cache = value)} selectedStoredSessionId="stored-a" />)

    act(() => {
      cache.updateSessionState('rt-owner', state => ({ ...state, busy: true }), 'stored-a')
    })

    act(() => {
      cache.ensureSessionState('rt-rebuilt', 'stored-a')
    })

    expect(cache.runtimeIdByStoredSessionIdRef.current.get('stored-a')).toBe('rt-owner')
    expect(cache.getRuntimeIdForStoredSession('stored-a')).toBe('rt-owner')
    // The rebuilt runtime still gets an entry of its own — its events (and its
    // own transcript) keep landing there — it just does not inherit the send
    // target of the conversation the other runtime is still serving.
    expect(cache.sessionStateByRuntimeIdRef.current.get('rt-rebuilt')?.storedSessionId).toBe('stored-a')
  })

  // PROTECTION: the take-over the guard must not close off. Once the old runtime
  // is gone — evicted/reaped, which drops its entry and, with it, its reverse
  // binding (onEvict) — the replacement that keeps announcing the pair claims it
  // on its own next observation.
  it('⑤ a replacement claims the stored id once its holder is gone', () => {
    let cache!: Cache

    setActiveSessionId('rt-owner')
    render(<Harness activeSessionId="rt-owner" onReady={value => (cache = value)} selectedStoredSessionId="stored-a" />)

    act(() => {
      cache.updateSessionState('rt-owner', state => ({ ...state, busy: true }), 'stored-a')
      cache.ensureSessionState('rt-rebuilt', 'stored-a')
    })

    expect(cache.getRuntimeIdForStoredSession('stored-a')).toBe('rt-owner')

    act(() => {
      // What onEvict does for a reaped runtime: state and binding go together.
      cache.sessionStateByRuntimeIdRef.current.delete('rt-owner')
      cache.runtimeIdByStoredSessionIdRef.current.delete('stored-a')
    })

    act(() => {
      cache.ensureSessionState('rt-rebuilt', 'stored-a')
    })

    expect(cache.runtimeIdByStoredSessionIdRef.current.get('stored-a')).toBe('rt-rebuilt')
    expect(cache.getRuntimeIdForStoredSession('stored-a')).toBe('rt-rebuilt')
  })
})

function Harness({ activeSessionId, onReady, selectedStoredSessionId }: HarnessProps) {
  const busyRef: MutableRefObject<boolean> = { current: false }

  const cache = useSessionStateCache({
    activeSessionId,
    busyRef,
    selectedStoredSessionId,
    setAwaitingResponse: () => undefined,
    setBusy: () => undefined,
    setMessages: () => undefined
  })

  onReady(cache)

  return null
}

describe('useSessionStateCache — per-session turn timer', () => {
  beforeEach(() => {
    // The view-sync flush runs on a real rAF in the browser path; in jsdom we
    // want it synchronous so the global mirror is observable immediately. The
    // hook closes over `window.requestAnimationFrame`, so stub that exact ref.
    // Return null (not a handle) so the hook's `viewSyncRafRef.current = rAF(...)`
    // assignment doesn't overwrite the null the synchronous callback just set —
    // otherwise the ref reads truthy and the NEXT sync is suppressed (a real
    // browser returns a handle but runs the callback async, so this race is a
    // test-only artifact of firing synchronously).
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0)

      return null as unknown as number
    })
    setTurnStartedAt(null)
    setCurrentModel('')
    setCurrentProvider('')
    setCurrentReasoningEffort('')
    setCurrentServiceTier('')
    setCurrentFastMode(false)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    setTurnStartedAt(null)
    setCurrentModel('')
    setCurrentProvider('')
    setCurrentReasoningEffort('')
    setCurrentServiceTier('')
    setCurrentFastMode(false)
  })

  it("keeps a background session's running turn clock and never mirrors it to the view", () => {
    let cache!: Cache
    // Active session is "fg-runtime"; the turn starts on the BACKGROUND session.
    render(<Harness activeSessionId="fg-runtime" onReady={c => (cache = c)} selectedStoredSessionId="fg-stored" />)

    const startedAt = 1_700_000_000_000

    act(() => {
      cache.updateSessionState('bg-runtime', state => ({ ...state, busy: true, turnStartedAt: startedAt }), 'bg-stored')
    })

    // The background session's own cache entry holds the clock...
    expect(cache.sessionStateByRuntimeIdRef.current.get('bg-runtime')?.turnStartedAt).toBe(startedAt)
    // ...but the global atom (statusbar timer) is untouched — a background turn
    // must not drive the foreground timer.
    expect($turnStartedAt.get()).toBeNull()
  })

  it("mirrors the focused session's turn clock into the global atom on view-sync", () => {
    let cache!: Cache
    render(<Harness activeSessionId="fg-runtime" onReady={c => (cache = c)} selectedStoredSessionId="fg-stored" />)

    const startedAt = 1_700_000_111_000

    // A turn on the ACTIVE session stages into the view; the flush mirrors its
    // turnStartedAt into the global atom the statusbar reads.
    act(() => {
      cache.updateSessionState('fg-runtime', state => ({ ...state, busy: true, turnStartedAt: startedAt }), 'fg-stored')
    })

    expect($turnStartedAt.get()).toBe(startedAt)
  })

  it('clears the global clock when the focused turn ends', () => {
    let cache!: Cache
    render(<Harness activeSessionId="fg-runtime" onReady={c => (cache = c)} selectedStoredSessionId="fg-stored" />)

    act(() => {
      cache.updateSessionState(
        'fg-runtime',
        state => ({ ...state, busy: true, turnStartedAt: 1_700_000_222_000 }),
        'fg-stored'
      )
    })
    expect($turnStartedAt.get()).toBe(1_700_000_222_000)

    act(() => {
      cache.updateSessionState('fg-runtime', state => ({ ...state, busy: false, turnStartedAt: null }))
    })
    expect($turnStartedAt.get()).toBeNull()
  })

  it('mirrors the focused session model metadata when switching from a cached session', () => {
    let cache!: Cache

    const { rerender } = render(
      <Harness activeSessionId="fg-runtime" onReady={c => (cache = c)} selectedStoredSessionId="fg-stored" />
    )

    act(() => {
      cache.updateSessionState(
        'bg-runtime',
        state => ({
          ...state,
          fast: true,
          model: 'anthropic/claude-opus-4.8',
          provider: 'anthropic',
          reasoningEffort: 'high',
          serviceTier: 'priority'
        }),
        'bg-stored'
      )
    })

    // Background metadata is cached but must not bleed into the visible statusbar.
    expect($currentModel.get()).toBe('')
    expect($currentReasoningEffort.get()).toBe('')
    expect($currentFastMode.get()).toBe(false)

    rerender(<Harness activeSessionId="bg-runtime" onReady={c => (cache = c)} selectedStoredSessionId="bg-stored" />)

    const bgState = cache.sessionStateByRuntimeIdRef.current.get('bg-runtime')
    expect(bgState).toBeTruthy()

    act(() => {
      cache.syncSessionStateToView('bg-runtime', bgState!)
    })

    expect($currentModel.get()).toBe('anthropic/claude-opus-4.8')
    expect($currentProvider.get()).toBe('anthropic')
    expect($currentReasoningEffort.get()).toBe('high')
    expect($currentServiceTier.get()).toBe('priority')
    expect($currentFastMode.get()).toBe(true)
  })

  it('clears stale model metadata when the newly focused session has no cached value', () => {
    setCurrentModel('previous-model')
    setCurrentProvider('previous-provider')
    setCurrentReasoningEffort('high')
    setCurrentServiceTier('priority')
    setCurrentFastMode(true)

    let cache!: Cache

    const { rerender } = render(
      <Harness activeSessionId="fg-runtime" onReady={c => (cache = c)} selectedStoredSessionId="fg-stored" />
    )

    act(() => {
      cache.updateSessionState('bg-runtime', state => ({ ...state }), 'bg-stored')
    })

    rerender(<Harness activeSessionId="bg-runtime" onReady={c => (cache = c)} selectedStoredSessionId="bg-stored" />)

    const bgState = cache.sessionStateByRuntimeIdRef.current.get('bg-runtime')
    expect(bgState).toBeTruthy()

    act(() => {
      cache.syncSessionStateToView('bg-runtime', bgState!)
    })

    expect($currentModel.get()).toBe('')
    expect($currentProvider.get()).toBe('')
    expect($currentReasoningEffort.get()).toBe('')
    expect($currentServiceTier.get()).toBe('')
    expect($currentFastMode.get()).toBe(false)
  })
})

interface LayoutProbeHarnessProps {
  activeSessionId: string | null
  onLayoutSnapshot: (snapshot: { active: string | null; selected: string | null }) => void
  onReady: (cache: Cache) => void
  selectedStoredSessionId: string | null
}

function LayoutProbeHarness({
  activeSessionId,
  onLayoutSnapshot,
  onReady,
  selectedStoredSessionId
}: LayoutProbeHarnessProps) {
  const busyRef: MutableRefObject<boolean> = { current: false }

  const cache = useSessionStateCache({
    activeSessionId,
    busyRef,
    selectedStoredSessionId,
    setAwaitingResponse: () => undefined,
    setBusy: () => undefined,
    setMessages: () => undefined
  })

  onReady(cache)

  // useLayoutEffect fires synchronously right after the DOM commit, BEFORE
  // the hook's own useEffect (a passive effect) has a chance to mirror the
  // new props into activeSessionIdRef/selectedStoredSessionIdRef. Anything
  // that reads the refs in this window — including a synchronous DOM event
  // handler firing against the just-committed view — observes the outgoing
  // session's ids.
  useLayoutEffect(() => {
    onLayoutSnapshot({
      active: cache.activeSessionIdRef.current,
      selected: cache.selectedStoredSessionIdRef.current
    })
  })

  return null
}

describe('useSessionStateCache — refs stay coherent with the committed session on switch (#59305)', () => {
  afterEach(() => cleanup())

  it('reflects the new session ids from the layout phase right after switching to a new session', () => {
    let cache!: Cache
    const snapshots: Array<{ active: string | null; selected: string | null }> = []

    const { rerender } = render(
      <LayoutProbeHarness
        activeSessionId="runtime-A"
        onLayoutSnapshot={s => snapshots.push(s)}
        onReady={c => (cache = c)}
        selectedStoredSessionId="stored-A"
      />
    )

    void cache
    snapshots.length = 0 // drop the initial-mount snapshot; only the switch matters

    rerender(
      <LayoutProbeHarness
        activeSessionId="runtime-B"
        onLayoutSnapshot={s => snapshots.push(s)}
        onReady={c => (cache = c)}
        selectedStoredSessionId="stored-B"
      />
    )

    // The refs must already reflect B by the layout phase — a callback firing
    // in this window must never observe the outgoing session's ids.
    expect(snapshots[0]).toEqual({ active: 'runtime-B', selected: 'stored-B' })
  })

  it('does not clobber an imperative ref pin on a re-render that leaves the props unchanged (#54527-class)', () => {
    // submit.ts pins activeSessionIdRef.current to a freshly resumed runtime id
    // WITHOUT updating the source atom that feeds the activeSessionId prop (by
    // design — see submit.ts's "pin the foreground session context" comment).
    // The prop-mirroring here must only fire when the prop itself changes; an
    // unconditional resync would silently undo that pin on the next incidental
    // render (wiring.tsx re-renders constantly during an active turn).
    let cache!: Cache

    const { rerender } = render(
      <Harness activeSessionId="runtime-A" onReady={c => (cache = c)} selectedStoredSessionId="stored-A" />
    )

    // Simulate submit.ts's imperative pin: a resume swapped in a new runtime
    // id without touching the prop.
    cache.activeSessionIdRef.current = 'runtime-resumed'

    // A re-render with the SAME props (e.g. an unrelated $busy/$messages
    // change elsewhere in the tree) must not touch the pinned ref.
    rerender(<Harness activeSessionId="runtime-A" onReady={c => (cache = c)} selectedStoredSessionId="stored-A" />)

    expect(cache.activeSessionIdRef.current).toBe('runtime-resumed')

    // A genuine prop change (a real navigation/selection move) still wins.
    rerender(<Harness activeSessionId="runtime-B" onReady={c => (cache = c)} selectedStoredSessionId="stored-B" />)

    expect(cache.activeSessionIdRef.current).toBe('runtime-B')
  })
})

function userMessage(id: string, text: string): ChatMessage {
  return { id, role: 'user', parts: [{ type: 'text', text }] }
}

function assistantText(id: string, text: string): ChatMessage {
  return { id, role: 'assistant', parts: [{ type: 'text', text }] }
}

function assistantError(id: string, error: string): ChatMessage {
  return { id, role: 'assistant', parts: [], error, pending: false }
}

function transcriptForCache(id: string): ChatMessage[] {
  return [userMessage(`${id}-user`, id), assistantText(`${id}-assistant`, `reply ${id}`)]
}

interface ViewHarnessProps {
  activeSessionId: string | null
  onReady: (cache: Cache) => void
}

function ViewHarness({ activeSessionId, onReady }: ViewHarnessProps) {
  const busyRef: MutableRefObject<boolean> = { current: false }

  const cache = useSessionStateCache({
    activeSessionId,
    busyRef,
    selectedStoredSessionId: null,
    setAwaitingResponse: () => undefined,
    setBusy: () => undefined,
    // Wire the published view back into the real $messages atom the flush
    // reads from, so the round-trip matches production.
    setMessages: messages => $messages.set(messages)
  })

  onReady(cache)

  return null
}

describe('useSessionStateCache — cross-thread error isolation', () => {
  afterEach(() => {
    cleanup()
    $messages.set([])
    $sessionStates.set({})
  })

  it('does not leak a failed turn into another thread on switch', () => {
    $messages.set([])
    let cache!: Cache
    const { rerender } = render(<ViewHarness activeSessionId="thread-A" onReady={c => (cache = c)} />)

    // Thread A ends its turn with an out-of-funds error and is on screen.
    act(() => {
      cache.updateSessionState(
        'thread-A',
        state => ({
          ...state,
          busy: false,
          messages: [userMessage('user-a', 'do the thing'), assistantError('assistant-a-error', 'Out of funds')]
        }),
        'stored-A'
      )
    })

    expect($messages.get().some(message => message.error === 'Out of funds')).toBe(true)

    // Switch to thread B (which completed cleanly). Its cached state syncs to
    // the view while $messages still holds thread A's transcript.
    rerender(<ViewHarness activeSessionId="thread-B" onReady={c => (cache = c)} />)
    act(() => {
      cache.updateSessionState(
        'thread-B',
        state => ({
          ...state,
          busy: false,
          messages: [userMessage('user-b', 'hello'), assistantText('assistant-b', 'hi there')]
        }),
        'stored-B'
      )
    })

    expect($messages.get().map(message => message.id)).toEqual(['user-b', 'assistant-b'])
    expect($messages.get().some(message => message.error === 'Out of funds')).toBe(false)
  })

  it('still preserves a same-session local error a heartbeat dropped', () => {
    $messages.set([])
    let cache!: Cache
    render(<ViewHarness activeSessionId="thread-A" onReady={c => (cache = c)} />)

    // First paint establishes thread A as the on-screen session.
    act(() => {
      cache.updateSessionState(
        'thread-A',
        state => ({ ...state, busy: false, messages: [userMessage('user-a', 'do the thing')] }),
        'stored-A'
      )
    })

    // A local error lands in the view (e.g. failAssistantMessage wrote it).
    $messages.set([userMessage('user-a', 'do the thing'), assistantError('assistant-a-error', 'OpenRouter 403')])

    // A later same-session heartbeat carries cached state that lost the error.
    act(() => {
      cache.updateSessionState('thread-A', state => ({
        ...state,
        busy: false,
        messages: [userMessage('user-a', 'do the thing')]
      }))
    })

    expect($messages.get().some(message => message.error === 'OpenRouter 403')).toBe(true)
  })

  it('evicts the oldest warm transcript with its reverse ownership while retaining lightweight state', () => {
    let cache!: Cache
    render(<Harness activeSessionId={null} onReady={value => (cache = value)} selectedStoredSessionId={null} />)

    act(() => {
      for (let index = 0; index < 25; index += 1) {
        cache.updateSessionState(
          `runtime-${index}`,
          state => ({ ...state, messages: transcriptForCache(`message-${index}`) }),
          `stored-${index}`
        )
      }
    })

    expect(cache.sessionStateByRuntimeIdRef.current.has('runtime-0')).toBe(false)
    expect(cache.runtimeIdByStoredSessionIdRef.current.has('stored-0')).toBe(false)
    expect($sessionStates.get()['runtime-0']).toMatchObject({ storedSessionId: 'stored-0', busy: false })
    expect($sessionStates.get()['runtime-0']?.messages).toEqual([])
    expect(cache.getRuntimeIdForStoredSession('stored-24')).toBe('runtime-24')
  })

  it('only returns a runtime whose cached state owns the requested stored session', () => {
    let cache!: Cache
    render(<Harness activeSessionId={null} onReady={value => (cache = value)} selectedStoredSessionId={null} />)

    act(() => {
      cache.ensureSessionState('runtime-A', 'stored-A')
      cache.ensureSessionState('runtime-B', 'stored-B')
    })

    expect(cache.getRuntimeIdForStoredSession('stored-A')).toBe('runtime-A')
    expect(cache.getRuntimeIdForStoredSession('missing')).toBeNull()

    // Simulate a recycled/cross-wired map entry. The reverse state ownership
    // check must reject it instead of allowing a submit into stored-B.
    cache.runtimeIdByStoredSessionIdRef.current.set('stored-A', 'runtime-B')
    expect(cache.getRuntimeIdForStoredSession('stored-A')).toBeNull()
  })

  describe('reconnect-orphaned transcripts (#95189)', () => {
    // Unique per-test runtime/stored ids: earlier describes in this file leave
    // states in $sessionStates, and a recycled id would let this test's
    // authority probe read THEIR stale flags instead of its own.
    const bg = 'orphan-bg-runtime'
    const fg = 'orphan-fg-runtime'
    const bgStored = 'orphan-bg-stored'
    const fgStored = 'orphan-fg-stored'

    beforeEach(() => {
      $sessionStates.set({})
      setActiveSessionId(null)
    })

    afterEach(() => {
      $sessionStates.set({})
      setActiveSessionId(null)
    })

    it('releases a busy transcript once reconciliation settles the authoritative record', () => {
      let cache!: Cache

      render(<Harness activeSessionId={fg} onReady={value => (cache = value)} selectedStoredSessionId={fgStored} />)

      act(() => {
        // A mid-turn session carries a growing transcript: without messages
        // there would be nothing warm to release.
        cache.updateSessionState(
          bg,
          state => ({
            ...state,
            busy: true,
            messages: [
              { id: `${bg}-user`, role: 'user', parts: [{ type: 'text', text: 'hello' }] },
              { id: `${bg}-assistant`, role: 'assistant', parts: [{ type: 'text', text: 'partial reply' }] }
            ]
          }),
          bgStored
        )
      })

      expect($sessionStates.get()[bg]?.busy).toBe(true)
      expect(cache.sessionStateByRuntimeIdRef.current.has(bg)).toBe(true)

      act(() => {
        // The minting socket died mid-turn; reconnect reconciliation
        // (reconcileBusyStatesOnReconnect) downgrades the authoritative
        // record, and the respawned backend re-mints runtime ids so no event
        // will ever settle this snapshot's own busy flag again.
        const states = $sessionStates.get()

        $sessionStates.set({
          ...states,
          [bg]: { ...states[bg]!, busy: false, awaitingResponse: false }
        })
      })

      // Production caches are bounded by the class defaults (24 sessions /
      // 32MB), and prune only drains once that budget is exceeded. Simulate
      // the reconnect churn of #95189: a stream of settled sessions pushes
      // the cache past its cap, and the orphaned entry — oldest touched,
      // finally warm-eligible now that the authoritative record settled —
      // must be the first thing drained, ownership included.
      const liveBusy = `${bg}-still-working`

      act(() => {
        cache.updateSessionState(
          liveBusy,
          state => ({
            ...state,
            busy: true,
            messages: [{ id: `${liveBusy}-u`, role: 'user', parts: [{ type: 'text', text: 'long turn' }] }]
          }),
          `${liveBusy}-stored`
        )
      })

      for (let i = 0; i < 24; i += 1) {
        act(() => {
          cache.updateSessionState(
            `${bg}-churn-${i}`,
            state => ({
              ...state,
              messages: [{ id: `churn-${i}`, role: 'user', parts: [{ type: 'text', text: `done ${i}` }] }]
            }),
            `${bg}-churn-${i}-stored`
          )
        })
      }

      expect(cache.sessionStateByRuntimeIdRef.current.has(bg)).toBe(false)
      expect(cache.runtimeIdByStoredSessionIdRef.current.has(bgStored)).toBe(false)
      // A genuinely running turn is never a casualty of the drain.
      expect(cache.sessionStateByRuntimeIdRef.current.has(liveBusy)).toBe(true)
    })

    it('keeps a live background turn cached while its authoritative record is busy', () => {
      let cache!: Cache

      render(<Harness activeSessionId={fg} onReady={value => (cache = value)} selectedStoredSessionId={fgStored} />)

      act(() => {
        cache.updateSessionState(bg, state => ({ ...state, busy: true }), bgStored)
      })

      act(() => {
        cache.updateSessionState(fg, state => ({ ...state, model: 'test/model' }))
      })

      expect(cache.sessionStateByRuntimeIdRef.current.has(bg)).toBe(true)
      expect(cache.runtimeIdByStoredSessionIdRef.current.get(bgStored)).toBe(bg)
    })
  })
})

// #93059: reconnect used to downgrade the $sessionStates mirror only, leaving
// this cache (which warm resume ORs over `running: false`) still busy.
describe('useSessionStateCache — reconnect busy reconcile (#93059)', () => {
  // Only retireBusyClaim is reachable from the store.
  const asDelegate = (partial: Partial<SessionTileDelegate>) => partial as SessionTileDelegate

  // Stands in for "no wiring mounted": every claim is a miss, nothing written.
  const inertDelegate = asDelegate({ retireBusyClaim: () => false })

  afterEach(() => {
    cleanup()
    setSessionTileDelegate(inertDelegate)
    clearAllSessionStates()
    setActiveSessionId(null)
  })

  it('retires the wiring cache entry, not just the store mirror', () => {
    let cache!: Cache

    setActiveSessionId('runtime-1')
    render(
      <Harness activeSessionId="runtime-1" onReady={value => (cache = value)} selectedStoredSessionId="stored-1" />
    )

    // The wiring layer's own retireBusyClaim, over the REAL updateSessionState.
    setSessionTileDelegate(
      asDelegate({
        retireBusyClaim: runtimeId => {
          cache.updateSessionState(runtimeId, state => ({ ...state, awaitingResponse: false, busy: false }))

          return true
        }
      })
    )

    act(() => {
      cache.updateSessionState('runtime-1', state => ({ ...state, awaitingResponse: true, busy: true }), 'stored-1')
    })

    expect(cache.sessionStateByRuntimeIdRef.current.get('runtime-1')?.busy).toBe(true)

    // Backend respawned: no terminal busy:false can arrive for this runtime.
    act(() => reconcileBusyStatesOnReconnect())

    expect(cache.sessionStateByRuntimeIdRef.current.get('runtime-1')?.busy).toBe(false)
    expect(cache.sessionStateByRuntimeIdRef.current.get('runtime-1')?.awaitingResponse).toBe(false)
    expect($sessionStates.get()['runtime-1']?.busy).toBe(false)
  })
})
