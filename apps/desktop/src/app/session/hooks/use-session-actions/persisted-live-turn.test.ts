// Focused locator cases for the shape isolated in the #124928 review thread: a
// mid-turn redirect that interrupts streaming narration, so the streamed text
// persists as a text-only assistant row directly before the correction.
import { expect, it } from 'vitest'

import { type ChatMessage, chatMessageText, toChatMessages } from '@/lib/chat-messages'
import type { SessionMessage, SessionResumeResult } from '@/types/hermes'

import { reconcilePersistedLiveTurn } from './persisted-live-turn'

const PROMPT = 'Please audit the widget module and report.'
const INTERRUPTED = 'Checking how the loader resolves plugins before'
const CORRECTION = 'Actually, look at the parser first.'
const AFTER = 'Switching to the parser as asked.'

const toolCall = (id: string) => ({ id, type: 'function' as const, function: { name: 'terminal', arguments: '{}' } })

const rows: SessionMessage[] = [
  { id: 100, role: 'user', content: PROMPT },
  {
    id: 102,
    role: 'assistant',
    content: 'Reading the module layout first.',
    tool_calls: [toolCall('a1')]
  } as SessionMessage,
  { id: 103, role: 'tool', content: 'result a1', tool_call_id: 'a1' },
  // Text-only: the narration the redirect cut off. Its correction follows it.
  { id: 115, role: 'assistant', content: INTERRUPTED } as SessionMessage,
  { id: 116, role: 'user', content: CORRECTION },
  {
    id: 122,
    role: 'assistant',
    content: AFTER,
    tool_calls: [toolCall('b1')]
  } as SessionMessage,
  { id: 129, role: 'tool', content: 'result b1', tool_call_id: 'b1' }
]

const INTERVAL_0 = `Reading the module layout first.\n\n${INTERRUPTED}`

const projection = (): Pick<SessionResumeResult, 'inflight' | 'queued' | 'session_id'> => ({
  session_id: 'rt-locator',
  inflight: {
    user: PROMPT,
    assistant: `${INTERVAL_0}\n\n${AFTER}`,
    streaming: true,
    corrections: [CORRECTION],
    correction_offsets: [Array.from(INTERVAL_0).length]
  }
})

const textOccurrences = (messages: ChatMessage[], needle: string) =>
  messages
    .filter(message => message.role === 'assistant')
    .flatMap(message => message.parts)
    .filter(part => part.type === 'text' && part.text.includes(needle)).length

const toolOccurrences = (messages: ChatMessage[], id: string) =>
  messages.flatMap(message => message.parts).filter(part => part.type === 'tool-call' && part.toolCallId === id).length

it('locates the running turn when a redirect interrupted streaming narration', () => {
  const messages = toChatMessages(rows)
  const result = reconcilePersistedLiveTurn(messages, [], rows, projection())

  expect(result).not.toBeNull()

  const settled = result!
  expect(settled.filter(message => message.role === 'user' && chatMessageText(message).includes(PROMPT))).toHaveLength(1)
  expect(settled.filter(message => message.role === 'user' && chatMessageText(message).includes(CORRECTION))).toHaveLength(1)
  expect(textOccurrences(settled, INTERRUPTED)).toBe(1)
  expect(textOccurrences(settled, AFTER)).toBe(1)
  expect(toolOccurrences(settled, 'a1')).toBe(1)
  expect(toolOccurrences(settled, 'b1')).toBe(1)
})

it('still reconciles a steered turn whose narration was sealed with its tools', () => {
  const sealed: SessionMessage[] = [
    {
      id: 100,
      role: 'user',
      content: PROMPT
    },
    {
      id: 102,
      role: 'assistant',
      content: 'Reading the module layout first.',
      tool_calls: [toolCall('a1')]
    } as SessionMessage,
    { id: 103, role: 'tool', content: 'result a1', tool_call_id: 'a1' },
    { id: 116, role: 'user', content: CORRECTION },
    {
      id: 122,
      role: 'assistant',
      content: AFTER,
      tool_calls: [toolCall('b1')]
    } as SessionMessage,
    { id: 129, role: 'tool', content: 'result b1', tool_call_id: 'b1' }
  ]

  const messages = toChatMessages(sealed)

  const result = reconcilePersistedLiveTurn(messages, [], sealed, {
    session_id: 'rt-locator',
    inflight: {
      user: PROMPT,
      assistant: `Reading the module layout first.\n\n${AFTER}`,
      streaming: true,
      corrections: [CORRECTION],
      correction_offsets: [Array.from('Reading the module layout first.').length]
    }
  })

  expect(result).not.toBeNull()
  expect(result!.filter(message => message.role === 'user' && chatMessageText(message).includes(PROMPT))).toHaveLength(1)
  expect(textOccurrences(result!, AFTER)).toBe(1)
  expect(toolOccurrences(result!, 'b1')).toBe(1)
})
