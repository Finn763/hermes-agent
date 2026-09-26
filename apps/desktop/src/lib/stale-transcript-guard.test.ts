import { describe, expect, it } from 'vitest'

import { type ChatMessage, textPart } from './chat-messages'
import { messagesIfTranscriptBehind } from './stale-transcript-guard'

const row = (id: string, role: 'assistant' | 'user', text: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id,
  role,
  parts: [textPart(text)],
  ...extra
})

describe('messagesIfTranscriptBehind', () => {
  it('does not report behind when the local view never anchored on the transcript (#123622)', () => {
    // Fresh session: the initial timeline/messages reads 404'd because the
    // session row is only persisted after the first turn, so the window holds
    // streamed rows with no durable rowId. The next successful read is longer
    // but proves nothing about another window — it must not trip the guard.
    const local = [row('user-1', 'user', 'hello'), row('stream-a1', 'assistant', 'hi there')]
    const remote = [
      row('1-0-user', 'user', 'hello', { rowId: 1 }),
      row('2-1-assistant', 'assistant', 'hi there', { rowId: 2 })
    ]

    expect(messagesIfTranscriptBehind(local, remote)).toBeNull()
  })

  it('does not report behind when no durable row overlaps', () => {
    const local = [row('local-u', 'user', 'hello', { rowId: 7 })]
    const remote = [
      row('1-0-user', 'user', 'hello', { rowId: 1 }),
      row('2-1-assistant', 'assistant', 'hi', { rowId: 2 })
    ]

    expect(messagesIfTranscriptBehind(local, remote)).toBeNull()
  })

  it('still reports behind when another window appended past shared rows (#65047)', () => {
    const local = [
      row('1-0-user', 'user', 'hello', { rowId: 1 }),
      row('2-1-assistant', 'assistant', 'hi', { rowId: 2 })
    ]
    const remote = [
      ...local,
      row('3-2-user', 'user', 'other window here', { rowId: 3 }),
      row('4-3-assistant', 'assistant', 'ack', { rowId: 4 })
    ]

    expect(messagesIfTranscriptBehind(local, remote)).toEqual(remote)
  })

  it('returns the remote page when local is empty and null when remote is empty', () => {
    const remote = [row('1-0-user', 'user', 'hello', { rowId: 1 })]

    expect(messagesIfTranscriptBehind([], remote)).toEqual(remote)
    expect(messagesIfTranscriptBehind([row('user-1', 'user', 'hello')], [])).toBeNull()
  })

  it('returns null when the views match', () => {
    const messages = [
      row('1-0-user', 'user', 'hello', { rowId: 1 }),
      row('2-1-assistant', 'assistant', 'hi', { rowId: 2 })
    ]

    expect(messagesIfTranscriptBehind(messages, messages)).toBeNull()
  })
})
