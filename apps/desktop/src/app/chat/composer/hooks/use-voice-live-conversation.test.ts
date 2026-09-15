// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type * as VoiceLiveModule from '@/lib/voice-live'
import { chunkForCommentary, DEFAULT_IDLE_HANGUP_SECONDS, IDLE_HANGUP_REASON, toLiveHistory } from '@/lib/voice-live'
import { notify } from '@/store/notifications'
import { $voiceLiveIdleHangupSeconds } from '@/store/voice-prefs'

import { delegationPrompt, useVoiceLiveConversation } from './use-voice-live-conversation'

const { FakeSession } = vi.hoisted(() => {
  class FakeSession {
    static instances: FakeSession[] = []
    close = vi.fn()
    instruct = vi.fn()
    noteActivity = vi.fn()
    options: undefined | { idleHangupSeconds?: number }
    setMuted = vi.fn()
    speak = vi.fn()
    start = vi.fn(async () => {})
    think = vi.fn()

    constructor(
      public handlers: {
        onClosed: (reason: string, usageSeconds: null | number) => void
        onDelegation: (id: string, context: unknown[]) => void
        onError: (message: string, fatal: boolean) => void
        onSpeakingChange?: (speaking: boolean) => void
        onTranscript?: (fragment: { speaker: string; text: string }) => void
      },
      options?: { idleHangupSeconds?: number }
    ) {
      this.options = options
      FakeSession.instances.push(this)
    }
  }

  return { FakeSession }
})

vi.mock('@/lib/voice-live', async importOriginal => {
  const actual = await importOriginal<typeof VoiceLiveModule>()

  return { ...actual, VoiceLiveSession: FakeSession }
})

vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))

afterEach(() => {
  cleanup()
  FakeSession.instances = []
  $voiceLiveIdleHangupSeconds.set(DEFAULT_IDLE_HANGUP_SECONDS)
  vi.clearAllMocks()
})

describe('GPT-Live delegation → Hermes turn', () => {
  it('sends the latest user words as the turn and the exchange as model-only context', () => {
    // The delegation event carries no text: both are reconstructed from
    // transcript deltas, fragments of one speaker concatenated as received.
    const { context, prompt } = delegationPrompt([
      { endMs: 1000, speaker: 'assistant', startMs: 0, text: 'Hi, how ' },
      { endMs: 1500, speaker: 'assistant', startMs: 1000, text: 'can I help?' },
      { endMs: 2500, speaker: 'user', startMs: 1500, text: 'What is ' },
      { endMs: 3200, speaker: 'user', startMs: 2500, text: 'the weather in Paris?' }
    ])

    expect(prompt).toBe('What is the weather in Paris?')
    expect(context).toContain('Voice assistant: Hi, how can I help?')
    expect(context).toContain('User: What is the weather in Paris?')
  })

  it('splits a long reply into vendor-sized commentary appends on sentence boundaries', () => {
    const sentence = 'This is a sentence about the result. '
    const chunks = chunkForCommentary(sentence.repeat(80), 400)

    expect(chunks.length).toBeGreaterThan(1)
    expect(chunks.every(chunk => chunk.length <= 400)).toBe(true)
    expect(chunks.every(chunk => chunk.endsWith('.'))).toBe(true)
    expect(chunks.join(' ')).toBe(sentence.repeat(80).trim())
  })

  it('seeds the live session with the most recent text turns within budget', () => {
    const turns = Array.from({ length: 40 }, (_, index) => ({
      role: (index % 2 === 0 ? 'user' : 'assistant') as 'assistant' | 'user',
      text: `turn ${index}`
    }))

    const history = toLiveHistory(turns, 6)

    expect(history).toHaveLength(6)
    expect(history.at(-1)?.content[0]?.text).toBe('turn 39')
    expect(history[0]?.role).toBe('user')
    expect(history.find(m => m.role === 'assistant')?.content[0]?.type).toBe('output_text')
  })
})

function conversationProps() {
  return {
    busy: false,
    consumePendingResponse: vi.fn(),
    enabled: false,
    onFatalError: vi.fn(),
    onInterrupt: vi.fn(),
    onSubmit: vi.fn(),
    pendingResponse: () => null,
    seedHistory: () => []
  }
}

/** Open a live conversation (enabled flips false→true, as the composer does). */
async function openConversation(idleHangupSeconds?: number) {
  if (idleHangupSeconds != null) {
    $voiceLiveIdleHangupSeconds.set(idleHangupSeconds)
  }

  const props = conversationProps()
  const { rerender } = renderHook((next: typeof props) => useVoiceLiveConversation(next), { initialProps: props })

  rerender({ ...props, enabled: true })
  await waitFor(() => expect(FakeSession.instances).toHaveLength(1))

  return { props, session: FakeSession.instances[0] }
}

describe('GPT-Live idle hangup', () => {
  it('opens the session with the configured deadline', async () => {
    const { session } = await openConversation(45)

    expect(session.options?.idleHangupSeconds).toBe(45)
  })

  it('defaults the deadline when config never loaded', async () => {
    const { session } = await openConversation()

    expect(session.options?.idleHangupSeconds).toBe(DEFAULT_IDLE_HANGUP_SECONDS)
  })

  it('shows the idle notice and stops only the live voice, never the chat', async () => {
    const { props, session } = await openConversation()

    act(() => session.handlers.onClosed(IDLE_HANGUP_REASON, 612))

    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'voice-live-idle-hangup',
        kind: 'info',
        message: 'Live voice ended after idle timeout.',
        title: 'Live voice session ended'
      })
    )
    // The chat session and any in-flight turn are untouched.
    expect(props.onInterrupt).not.toHaveBeenCalled()
    expect(props.onSubmit).not.toHaveBeenCalled()
    // ...and the composer's voice conversation is closed out (mic, wake re-arm).
    expect(props.onFatalError).toHaveBeenCalledTimes(1)
  })

  it('still warns with the reason when the live connection drops', async () => {
    const { session } = await openConversation()

    act(() => session.handlers.onClosed('connection_lost', 12))

    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ kind: 'warning', message: 'connection_lost (12s)' }))
  })
})
