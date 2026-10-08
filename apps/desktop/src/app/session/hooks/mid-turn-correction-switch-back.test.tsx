// Repro from the #124928 review thread: a mid-turn redirect that interrupts
// streaming narration, then switching away from and back to the running session.
import type { GatewayEvent } from '@hermes/shared'
import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getLatestSessionMessages } from '@/hermes'
import { type ChatMessage, chatMessageText } from '@/lib/chat-messages'
import { resetInFlightTurnJournalStateForTests } from '@/lib/inflight-turn-journal'
import {
  _resetSessionOwnerHintsForTests,
  setActiveSessionId,
  setAwaitingResponse,
  setBusy,
  setMessages,
  setSelectedStoredSessionId,
  setSessions
} from '@/store/session'
import { clearAllSessionStates } from '@/store/session-states'
import type { SessionMessage, SessionResumeResult } from '@/types/hermes'

import { useMessageStream } from './use-message-stream'
import { STREAM_DELTA_FLUSH_MS } from './use-message-stream/utils'
import { usePromptActions } from './use-prompt-actions'
import { useSessionActions } from './use-session-actions'
import { useSessionStateCache } from './use-session-state-cache'

vi.mock('@/hermes', async original => ({
  ...(await original<Record<string, unknown>>()),
  getLatestSessionMessages: vi.fn()
}))
vi.mock('@/store/profile', async original => ({
  ...(await original<Record<string, unknown>>()),
  ensureGatewayProfile: vi.fn().mockResolvedValue(undefined)
}))

const STORED = 'stored-mid-turn-correction'
const RT = 'rt-mid-turn-correction'
const noop = async () => undefined

// ── synthetic turn (made-up text; ids/shape follow a real redirected, tool-heavy turn) ─────────────
const PROMPT = 'Please audit the widget module and report.'
const CORRECTION = 'Actually, look at the parser first.'

interface Round {
  /** assistant row id (stored when its tool calls start) */
  id: number
  text: string
  reasoning?: string
  calls: { id: string; name: string; result: string; row: number }[]
}

const call = (id: string, row: number, name = 'terminal') => ({ id, name, result: `result ${id}`, row })

// Before the correction: two narrated rounds, then a third narration that is still streaming (and is
// stored as a text-only row) when the user redirects.
const BEFORE: Round[] = [
  { id: 102, text: 'Reading the module layout first.', reasoning: 'plan', calls: [call('a1', 103), call('a2', 104)] },
  { id: 107, text: 'Layout understood, now the entry points.', reasoning: 'entry', calls: [call('a3', 108)] }
]

const PARTIAL = { id: 115, text: 'Checking how the loader resolves plugins before' }
const CORRECTION_ROW = 116

// After the correction: one narrated round per entry (`text` is sealed by message.interim).
const AFTER: Round[] = [
  { id: 122, text: 'Switching to the parser as asked.', reasoning: 'parser', calls: [call('b1', 129)] },
  { id: 134, text: '', reasoning: 'next', calls: [call('b2', 135)] },
  { id: 138, text: 'Tokenizer reads cleanly.', reasoning: 'tok', calls: [call('b3', 139), call('b4', 140)] },
  { id: 141, text: 'Grammar table looks consistent.', reasoning: 'gram', calls: [call('b5', 142)] },
  { id: 143, text: 'Error paths need one more look.', reasoning: 'err', calls: [call('b6', 144)] },
  { id: 145, text: 'Recovery logic is fine.', reasoning: 'rec', calls: [call('b7', 146)] }
]

const assistantRow = (round: Round, ts: number): SessionMessage =>
  ({
    id: round.id,
    role: 'assistant',
    content: round.text,
    ...(round.reasoning ? { reasoning: round.reasoning } : {}),
    tool_calls: round.calls.map(c => ({ id: c.id, type: 'function', function: { name: c.name, arguments: '{}' } })),
    timestamp: ts
  }) as SessionMessage

const toolRow = (c: Round['calls'][number], ts: number): SessionMessage => ({
  id: c.row,
  role: 'tool',
  content: c.result,
  tool_call_id: c.id,
  timestamp: ts
})

type Frame = [GatewayEvent['type'], Record<string, unknown>]

/** The turn as ordered (frame, stored-rows-after-frame) steps; the redirect is a marker step. */
function turn({ partial, redirect }: { partial: boolean; redirect: boolean }) {
  const rows: SessionMessage[] = [{ id: 100, role: 'user', content: PROMPT, timestamp: 1 }]
  const steps: { frame?: Frame; redirect?: true; rows: SessionMessage[] }[] = []
  let ts = 10
  const push = (frame: Frame) => steps.push({ frame, rows: [...rows] })
  // The agent prepends one paragraph break to the first text delta after a tool iteration (stream_delivery.py).
  let needsBreak = false

  const narrate = (text: string) => {
    push(['message.delta', { text: `${needsBreak ? '\n\n' : ''}${text}` }])
    needsBreak = false
  }

  const round = (r: Round) => {
    if (r.reasoning) {
      push(['reasoning.delta', { text: r.reasoning }])
    }

    if (r.text) {
      narrate(r.text)
      push(['message.interim', { already_streamed: true, text: r.text }])
    }

    ts += 1
    rows.push(assistantRow(r, ts))
    r.calls.forEach(c => push(['tool.start', { args: {}, name: c.name, tool_id: c.id }]))
    r.calls.forEach(c => {
      ts += 1
      rows.push(toolRow(c, ts))
      push(['tool.complete', { name: c.name, result: c.result, tool_id: c.id }])
    })
    needsBreak = true
  }

  push(['message.start', {}])
  BEFORE.forEach(round)

  if (redirect) {
    if (partial) {
      // A narration is streaming when the user redirects; the agent stores what it had as a text-only row.
      narrate(PARTIAL.text)
      rows.push({ id: PARTIAL.id, role: 'assistant', content: PARTIAL.text, timestamp: ts + 1 })
    }

    rows.push({ id: CORRECTION_ROW, role: 'user', content: CORRECTION, timestamp: ts + 2 })
    steps.push({ redirect: true, rows: [...rows] })
  }

  AFTER.forEach(round)

  return steps
}

const textsOf = (message: ChatMessage) => message.parts.filter(p => p.type === 'text').map(p => (p as { text: string }).text.trim())

const shape = (messages: ChatMessage[]) =>
  messages
    .map(m =>
      `${m.role === 'user' ? 'USER' : 'asst'} [${m.id.length > 30 ? `${m.id.slice(0, 27)}…` : m.id}${m.rowId !== undefined ? ` #${m.rowId}` : ''}${m.pending ? ' pending' : ''}${m.interim ? ' interim' : ''}] ` +
      m.parts
        .map(p =>
          p.type === 'text'
            ? `T(${p.text.trim().slice(0, 12).replace(/\n/g, '\\n')}${p.sourceRowId ? `@${p.sourceRowId}` : ''})`
            : p.type === 'tool-call'
              ? `tool:${p.toolCallId}${p.result === undefined ? '(running)' : ''}`
              : p.type === 'reasoning'
                ? 'R'
                : p.type
        )
        .join(' ')
    )
    .join('\n   ')

type RequestGateway = <T>(method: string, params?: Record<string, unknown>, timeoutMs?: number) => Promise<T>

function mount(requestGateway: RequestGateway) {
  const hook = renderHook(() => {
    const busyRef = useRef(false)
    const creatingSessionRef = useRef(false)
    const queryClient = useRef(new QueryClient()).current

    const cache = useSessionStateCache({
      activeSessionId: null,
      busyRef,
      selectedStoredSessionId: null,
      setAwaitingResponse,
      setBusy,
      setMessages
    })

    const actions = useSessionActions({
      ...cache,
      activeSessionId: null,
      busyRef,
      creatingSessionRef,
      getRouteToken: () => 'A',
      getRoutedStoredSessionId: () => null,
      navigate: vi.fn(),
      requestGateway,
      routedSessionId: null,
      selectedStoredSessionId: null
    })

    const stream = useMessageStream({
      ...cache,
      hydrateFromStoredSession: noop,
      queryClient,
      refreshHermesConfig: noop,
      refreshSessions: noop
    })

    const prompts = usePromptActions({
      activeSessionId: RT,
      activeSessionIdRef: cache.activeSessionIdRef,
      branchCurrentSession: async () => true,
      busyRef,
      createBackendSessionForSend: async () => RT,
      getRouteToken: () => 'A',
      getRoutedStoredSessionId: () => null,
      getRuntimeIdForStoredSession: () => null,
      handleSkinCommand: () => '',
      openMemoryGraph: () => undefined,
      refreshSessions: async () => undefined,
      requestGateway,
      resumeStoredSession: () => undefined,
      runtimeIdByStoredSessionIdRef: cache.runtimeIdByStoredSessionIdRef,
      selectedStoredSessionIdRef: cache.selectedStoredSessionIdRef,
      startFreshSessionDraft: () => undefined,
      sttEnabled: false,
      updateSessionState: cache.updateSessionState
    })

    return { actions, cache, prompts, stream }
  })

  return hook
}

interface Snapshot {
  label: string
  messages: ChatMessage[]
}

/**
 * Play the turn through the REAL stream reducer + redirect entry point, and leave and re-enter the running
 * session through the REAL warm `resumeSession` (session.activate with a live `inflight` projection, then a REST
 * page holding every row stored so far) at several moments of the turn.
 */
async function runTurn(options: { partial: boolean; redirect: boolean }): Promise<Snapshot[]> {
  const steps = turn(options)
  const snapshots: Snapshot[] = []
  let streamed = ''
  let corrections: string[] = []
  let offsets: number[] = []
  let rest: SessionMessage[] = []

  const requestGateway = vi.fn(async (method: string) => {
    if (method === 'session.redirect') {
      return { status: 'redirected' } as never
    }

    if (method === 'session.activate') {
      return {
        info: {},
        inflight: { assistant: streamed, correction_offsets: offsets, corrections, streaming: true, user: PROMPT },
        message_count: 0,
        messages: [],
        messages_omitted: true,
        resumed: STORED,
        running: true,
        session_id: RT,
        session_key: STORED
      } as unknown as SessionResumeResult as never
    }

    return {} as never
  })

  const hook = mount(requestGateway as unknown as RequestGateway)
  const { cache, prompts, stream } = hook.result.current

  cache.runtimeIdByStoredSessionIdRef.current.set(STORED, RT)
  cache.activeSessionIdRef.current = RT
  cache.selectedStoredSessionIdRef.current = STORED

  act(() => {
    cache.updateSessionState(
      RT,
      state => ({
        ...state,
        busy: true,
        messages: [{ id: 'user-1', parts: [{ text: PROMPT, type: 'text' }], role: 'user' }]
      }),
      STORED
    )
  })

  const state = () => cache.sessionStateByRuntimeIdRef.current.get(RT)!.messages

  const activate = async (label: string) => {
    vi.mocked(getLatestSessionMessages).mockResolvedValue({ messages: rest, session_id: STORED } as never)
    await act(async () => {
      await hook.result.current.actions.resumeSession(STORED, true)
    })
    snapshots.push({ label, messages: state() })
  }

  // The user is away between turn start and the first activation, and switches back at these moments.
  const switchBackAfter = new Set([
    'tool.start:b4', // a parallel batch is running
    'message.interim:Grammar table looks consistent.', // a narrated step just sealed
    'tool.start:b7' // the newest round is running
  ])

  for (const step of steps) {
    rest = step.rows

    if (step.redirect) {
      offsets = [Array.from(streamed).length]
      corrections = [CORRECTION]
      await act(async () => {
        await prompts.redirectPrompt(CORRECTION)
      })

      continue
    }

    const [type, payload] = step.frame!

    if (type === 'message.delta') {
      streamed += String(payload.text)
    }

    await act(async () => {
      stream.handleGatewayEvent({ payload, session_id: RT, type } as GatewayEvent)
      await new Promise(resolve => setTimeout(resolve, STREAM_DELTA_FLUSH_MS + 20))
    })

    const key = `${type}:${String(payload.tool_id ?? payload.text ?? '')}`

    if (switchBackAfter.has(key)) {
      await activate(key)
    }
  }

  await activate('end of turn (final tool result stored)')
  hook.unmount()

  return snapshots
}

// ── what the user can see ──────────────────────────────────────────────────────────────────────────
const visible = (messages: ChatMessage[]) => messages.filter(m => !m.hidden)

const userCopies = (messages: ChatMessage[], text: string) =>
  visible(messages).filter(m => m.role === 'user' && chatMessageText(m).includes(text)).length

/** tool call id -> number of painted copies (a tool call is one occurrence in the turn). */
const toolCopies = (messages: ChatMessage[]) => {
  const copies = new Map<string, number>()

  for (const m of visible(messages)) {
    for (const p of m.parts) {
      if (p.type === 'tool-call' && p.toolCallId) {
        copies.set(p.toolCallId, (copies.get(p.toolCallId) ?? 0) + 1)
      }
    }
  }

  return copies
}

const narrations = [...BEFORE, ...AFTER].map(r => r.text).filter(Boolean)

/** narrated step text -> painted copies (text parts that contain it; a flat dump of several steps counts for each). */
const narrationCopies = (messages: ChatMessage[]) =>
  new Map(
    narrations.map(text => [
      text,
      visible(messages)
        .filter(m => m.role === 'assistant')
        .flatMap(m => m.parts)
        .filter(p => p.type === 'text' && p.text.includes(text)).length
    ])
  )

const toolIdsOf = (message: ChatMessage) => message.parts.flatMap(p => (p.type === 'tool-call' && p.toolCallId ? [p.toolCallId] : []))

/**
 * Sealed live bubbles (`assistant-stream-<ms>-<n>`, interim) that a durable row already carries: they share tool
 * calls with a stored row AND every narration they hold is stored text. They are the same occurrence painted twice.
 */
const sealedBubblesRepeatingStoredRows = (messages: ChatMessage[]) => {
  const stored = messages.filter(m => m.role === 'assistant' && m.rowId !== undefined)
  const storedTools = new Set(stored.flatMap(toolIdsOf))
  const storedTexts = stored.flatMap(m => m.parts.flatMap(p => (p.type === 'text' ? [p.text] : [])))

  return messages
    .filter(m => m.role === 'assistant' && m.rowId === undefined && m.interim === true && m.pending !== true)
    .filter(m => /^assistant-stream-\d+-\d+$/.test(m.id))
    .filter(
      m =>
        toolIdsOf(m).some(id => storedTools.has(id)) &&
        m.parts.every(p => p.type !== 'text' || storedTexts.some(text => text.includes(p.text.trim())))
    )
    .map(m => `${m.id.slice(0, 22)}… (${toolIdsOf(m).join(',')})`)
}

const doubled = (copies: Map<string, number>) => [...copies].filter(([, n]) => n > 1).map(([k, n]) => `${k} x${n}`)

describe('a mid-turn correction, then switching away from and back to the running session', () => {
  beforeEach(() => {
    localStorage.clear()
    clearAllSessionStates()
    resetInFlightTurnJournalStateForTests()
    _resetSessionOwnerHintsForTests()
    setMessages([])
    setActiveSessionId(null)
    setSelectedStoredSessionId(null)
    setBusy(false)
    setAwaitingResponse(false)
    setSessions([
      {
        ended_at: null,
        id: STORED,
        input_tokens: 0,
        is_active: true,
        last_active: 1,
        message_count: 3,
        model: null,
        output_tokens: 0,
        preview: null,
        source: 'desktop',
        started_at: 1,
        title: STORED,
        tool_call_count: 0
      }
    ])
    vi.mocked(getLatestSessionMessages).mockReset()
  })

  afterEach(() => {
    cleanup()
    clearAllSessionStates()
    resetInFlightTurnJournalStateForTests()
    setSessions([])
    setMessages([])
    setActiveSessionId(null)
    setSelectedStoredSessionId(null)
    vi.restoreAllMocks()
  })

  // The redirect interrupted a narration that the agent had already stored as a text-only row — the shape of the
  // captured session. `partial: false` (correction lands between tool rounds) and `redirect: false` are controls.
  describe.each([
    { name: 'redirect interrupting a streaming narration', partial: true, redirect: true },
    { name: 'control: redirect between tool rounds (no text-only row)', partial: false, redirect: true },
    { name: 'control: no redirect', partial: false, redirect: false }
  ])('$name', options => {
    it('(a) paints the prompt that started the turn once', async () => {
      const snapshots = await runTurn(options)

      for (const { label, messages } of snapshots) {
        expect(
          userCopies(messages, PROMPT),
          `prompt painted twice after switch-back at ${label}\n   ${shape(messages)}`
        ).toBe(1)

        if (options.redirect) {
          expect(
            userCopies(messages, CORRECTION),
            `correction painted twice at ${label}\n   ${shape(messages)}`
          ).toBe(1)
        }
      }
    })

    it('(b) paints every narrated step and tool call once', async () => {
      const snapshots = await runTurn(options)

      for (const { label, messages } of snapshots) {
        expect(
          [...doubled(toolCopies(messages)), ...doubled(narrationCopies(messages))],
          `steps painted twice after switch-back at ${label}\n   ${shape(messages)}`
        ).toEqual([])
      }
    })

    it('(c) does not keep sealed interim bubbles beside the durable rows that already hold them', async () => {
      const snapshots = await runTurn(options)

      for (const { label, messages } of snapshots) {
        expect(
          sealedBubblesRepeatingStoredRows(messages),
          `sealed live bubbles repeat stored rows after switch-back at ${label}
   ${shape(messages)}`
        ).toEqual([])
      }
    })
  })
})
