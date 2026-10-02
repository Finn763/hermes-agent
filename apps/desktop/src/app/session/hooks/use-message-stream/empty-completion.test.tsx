import { act, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { chatMessageText } from '@/lib/chat-messages'

import { type MessageStreamHarness, renderMessageStream } from './test-harness'

const SID = 'session-1'

// ponytail: one empty-final case covers the wipe (long prompts,
// reasoning-heavy providers); add error-frame variants only if a new wipe
// path appears.
describe('empty message.complete preserves streamed text', () => {
  let stream: MessageStreamHarness
  let hydrate: ReturnType<typeof vi.fn>

  const mount = () => {
    hydrate = vi.fn(async () => undefined)
    stream = renderMessageStream(SID, { hydrateFromStoredSession: hydrate })
  }

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('keeps streamed text and skips hydration when final text is empty', async () => {
    mount()
    await act(() => stream.handleEvent({ payload: {}, session_id: SID, type: 'message.start' }))
    await act(() =>
      stream.handleEvent({ payload: { text: 'streamed answer' }, session_id: SID, type: 'message.delta' })
    )
    await act(() =>
      stream.handleEvent({ payload: { text: '' }, session_id: SID, type: 'message.complete' })
    )

    const bubble = [...stream.state().messages]
      .reverse()
      .find(m => m.role === 'assistant' && !m.hidden)
    expect(bubble).toBeDefined()
    expect(chatMessageText(bubble!)).toBe('streamed answer')
    expect(bubble!.pending).toBe(false)
    expect(hydrate).not.toHaveBeenCalled()
    expect(stream.state().busy).toBe(false)
  })
})
