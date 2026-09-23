/**
 * Cold bot switch acknowledges the target immediately (hermes-agent#120277).
 *
 * Clicking bot B while bot A's chat is on screen must publish B as the
 * pending open SYNCHRONOUSLY — the cold backend start (~seconds) plus the
 * registry round-trip complete later. Without that, the roster highlight
 * keeps following the stale focused owner (A) and the click reads as dead.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RosterRow } from './types'

const { openBotCanonicalChat, prepareBotSource } = vi.hoisted(() => ({
  openBotCanonicalChat: vi.fn(),
  prepareBotSource: vi.fn()
}))

vi.mock('./canonical-chat', () => ({
  CANONICAL_CHAT_TITLE: 'Bot Chat',
  ensureBotMetadata: vi.fn(async () => ({})),
  notifyBotOpenFailure: vi.fn(),
  openBotCanonicalChat,
  prepareBotSource,
  PROFILE_SESSION_LIST_LIMIT: 200
}))

const { host } = await import('@hermes/plugin-sdk')
const { $openBotChat, $pendingBotOpen } = await import('./bot-state')
const { openRosterBot } = await import('./roster-actions')

const botB = {
  connectionId: 'local',
  name: 'bravo',
  canonical_session: { id: 'b-chat', resolved_id: 'b-tip' }
} as RosterRow

const botA = {
  connectionId: 'local',
  name: 'alpha',
  canonical_session: { id: 'a-chat', resolved_id: 'a-tip' }
} as RosterRow

function deferred<T = void>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => {
    resolve = r
  })
  return { promise, resolve }
}

beforeEach(() => {
  vi.clearAllMocks()
  $openBotChat.set(null)
  $pendingBotOpen.set(null)
  // @ts-expect-error — restore the harness default (no focus verb).
  delete host.focusOpenWorkspaceSession
})

describe('cold bot switch publishes its target synchronously', () => {
  it('marks B pending before the backend/registry round-trip settles', async () => {
    const gate = deferred()
    prepareBotSource.mockReturnValueOnce(gate.promise)
    openBotCanonicalChat.mockResolvedValueOnce({ openedId: 'b-tip', registryId: 'b-chat' })

    const flight = openRosterBot(botB)

    // Synchronous: the click already happened, the backend has not answered.
    expect($pendingBotOpen.get()?.key).toBe('local::bravo')

    gate.resolve()
    await flight

    expect($pendingBotOpen.get()).toBeNull()
    expect($openBotChat.get()?.openedSessionId).toBe('b-tip')
  })

  it('a superseded flight never clears its successor’s pending mark', async () => {
    const gateA = deferred()
    const gateB = deferred()
    prepareBotSource.mockReturnValueOnce(gateA.promise).mockReturnValueOnce(gateB.promise)
    // Only B reaches the registry: A returns stale right after its source
    // resolves, so a single open result belongs to B unambiguously.
    openBotCanonicalChat.mockResolvedValue({ openedId: 'b-tip', registryId: 'b-chat' })

    const flightA = openRosterBot(botA)
    expect($pendingBotOpen.get()?.key).toBe('local::alpha')

    const flightB = openRosterBot(botB)
    expect($pendingBotOpen.get()?.key).toBe('local::bravo')

    gateA.resolve()
    await flightA
    // A settled stale — B is still hydrating, its mark must survive.
    expect($pendingBotOpen.get()?.key).toBe('local::bravo')

    gateB.resolve()
    await flightB
    expect($pendingBotOpen.get()).toBeNull()
    expect($openBotChat.get()?.openedSessionId).toBe('b-tip')
  })

  it('a failed open releases the pending mark', async () => {
    prepareBotSource.mockRejectedValueOnce(new Error('backend away'))

    await expect(openRosterBot(botB)).resolves.toBe(false)

    expect($pendingBotOpen.get()).toBeNull()
  })
})
