import { describe, expect, it } from 'vitest'

import {
  approvalReplaySessionId,
  gatewayEventRequiresSessionId,
  resolveGatewayEventSessionId,
  type GatewayEventSessionRoute
} from './gateway-events'

describe('gateway event routing', () => {
  // Tiny runner that threads the next-pin state between calls, so the test
  // expresses a real session lifecycle instead of a stack of snapshots.
  const runScenario = (
    steps: ReadonlyArray<{
      activeSessionId: null | string
      eventType: string
      explicitSessionId?: string
    }>
  ): readonly GatewayEventSessionRoute[] => {
    const out: GatewayEventSessionRoute[] = []
    let pins: readonly string[] = []

    for (const step of steps) {
      const r = resolveGatewayEventSessionId({
        activeSessionId: step.activeSessionId,
        eventType: step.eventType,
        explicitSessionId: step.explicitSessionId ?? '',
        unscopedStreamSessionIds: pins
      })
      pins = r.nextUnscopedStreamSessionIds
      out.push(r)
    }

    return out
  }
  it('rehydrates pending approvals on reconnect ready and resumed session info', () => {
    expect(approvalReplaySessionId('gateway.ready', 'active-1', null)).toBe('active-1')
    expect(approvalReplaySessionId('session.info', 'active-1', 'routed-1')).toBe('routed-1')
    expect(approvalReplaySessionId('message.delta', 'active-1', 'routed-1')).toBeNull()
  })

  it('drops only unscoped subagent events (genuinely background work)', () => {
    expect(gatewayEventRequiresSessionId('subagent.progress')).toBe(true)
    expect(gatewayEventRequiresSessionId('subagent.start')).toBe(true)
  })

  it('attributes unscoped foreground turn events to the active chat', () => {
    // These must NOT be dropped when unscoped — they are the focused turn's own
    // output, and dropping them loses the live response until a refetch (#42178).
    expect(gatewayEventRequiresSessionId('message.delta')).toBe(false)
    expect(gatewayEventRequiresSessionId('message.complete')).toBe(false)
    expect(gatewayEventRequiresSessionId('message.interim')).toBe(false)
    expect(gatewayEventRequiresSessionId('reasoning.delta')).toBe(false)
    expect(gatewayEventRequiresSessionId('tool.start')).toBe(false)
    expect(gatewayEventRequiresSessionId('approval.request')).toBe(false)
  })

  it('allows global events to remain unscoped', () => {
    expect(gatewayEventRequiresSessionId('gateway.ready')).toBe(false)
    expect(gatewayEventRequiresSessionId('preview.restart.progress')).toBe(false)
    expect(gatewayEventRequiresSessionId('session.info')).toBe(false)
    expect(gatewayEventRequiresSessionId(undefined)).toBe(false)
  })

  it('keeps unscoped stream events pinned to the session that started them', () => {
    const started = resolveGatewayEventSessionId({
      activeSessionId: 'session-a',
      eventType: 'message.start',
      explicitSessionId: '',
      unscopedStreamSessionIds: []
    })

    expect(started).toEqual({
      drop: false,
      nextUnscopedStreamSessionIds: ['session-a'],
      pinned: false,
      sessionId: 'session-a'
    })

    const delta = resolveGatewayEventSessionId({
      activeSessionId: 'session-b',
      eventType: 'message.delta',
      explicitSessionId: '',
      unscopedStreamSessionIds: started.nextUnscopedStreamSessionIds
    })

    expect(delta).toEqual({
      drop: false,
      nextUnscopedStreamSessionIds: ['session-a'],
      pinned: true,
      sessionId: 'session-a'
    })

    const completed = resolveGatewayEventSessionId({
      activeSessionId: 'session-b',
      eventType: 'message.complete',
      explicitSessionId: '',
      unscopedStreamSessionIds: delta.nextUnscopedStreamSessionIds
    })

    expect(completed).toEqual({
      drop: false,
      nextUnscopedStreamSessionIds: [],
      pinned: true,
      sessionId: 'session-a'
    })
  })

  it('does not let a second chat message.start clobber a concurrent stream pin', () => {
    // #108045 — the regression. A starts a turn, the user switches to B mid
    // stream, B's message.start arrives. The single shared pin was overwritten
    // to B and A's subsequent deltas painted onto B. Per-stream pins keep both.
    const [, bStart, aDeltaAfterClobber, aThinkingAfterClobber] = runScenario([
      { activeSessionId: 'session-a', eventType: 'message.start' },
      { activeSessionId: 'session-b', eventType: 'message.start' },
      { activeSessionId: 'session-b', eventType: 'message.delta' },
      { activeSessionId: 'session-b', eventType: 'thinking.delta' }
    ])

    // After B starts, both streams are pinned — neither owns the slot.
    expect(bStart.nextUnscopedStreamSessionIds).toEqual(['session-a', 'session-b'])
    expect(bStart.sessionId).toBe('session-b')

    // The focused chat (B) is in the pin set so unscoped events from its own
    // mid-stream turn route to B; but A's pin survives so A-tagged explicit
    // events (end events, sub-tagged deltas) keep working.
    expect(aDeltaAfterClobber.pinned).toBe(true)
    expect(aThinkingAfterClobber.pinned).toBe(true)
    expect(bStart.nextUnscopedStreamSessionIds).toContain('session-a')
  })

  it('routes a new unscoped stream start to the currently active session', () => {
    const routed = resolveGatewayEventSessionId({
      activeSessionId: 'session-b',
      eventType: 'message.start',
      explicitSessionId: '',
      unscopedStreamSessionIds: ['session-a']
    })

    // Session B owns its own start, but A's stream is still running and keeps
    // its pin — the second start adds, it does not take over.
    expect(routed).toEqual({
      drop: false,
      nextUnscopedStreamSessionIds: ['session-a', 'session-b'],
      pinned: false,
      sessionId: 'session-b'
    })
  })

  it('attributes an unpinned stream event to the active session without the pin flag', () => {
    // A late straggler (no pin left after the previous turn completed) falls
    // back to the active session. The handler drops this case when the target
    // session has no live turn — the straggler belongs to a turn that already
    // ended elsewhere (#43142 family).
    const routed = resolveGatewayEventSessionId({
      activeSessionId: 'session-b',
      eventType: 'thinking.delta',
      explicitSessionId: '',
      unscopedStreamSessionIds: []
    })

    expect(routed).toEqual({
      drop: false,
      nextUnscopedStreamSessionIds: [],
      pinned: false,
      sessionId: 'session-b'
    })
  })

  it('keeps explicit events scoped and retires only the matching pin on completion', () => {
    const routed = resolveGatewayEventSessionId({
      activeSessionId: 'session-b',
      eventType: 'message.complete',
      explicitSessionId: 'session-a',
      unscopedStreamSessionIds: ['session-a', 'session-b']
    })

    // A's pin retires (its turn ended), B's pin survives.
    expect(routed).toEqual({
      drop: false,
      nextUnscopedStreamSessionIds: ['session-b'],
      pinned: true,
      sessionId: 'session-a'
    })
  })

  it('drops an unscoped event when several streams are live and the focused chat is idle', () => {
    // A and B are mid-stream in the background. C is focused but idle. An
    // unscoped delta arrived: nothing in the event says which stream owns it.
    // Guessing is what grafts A's output onto B, so drop. (#108045 / #77826.)
    const routed = resolveGatewayEventSessionId({
      activeSessionId: 'session-c',
      eventType: 'message.delta',
      explicitSessionId: '',
      unscopedStreamSessionIds: ['session-a', 'session-b']
    })

    expect(routed).toEqual({
      drop: true,
      nextUnscopedStreamSessionIds: ['session-a', 'session-b'],
      pinned: false,
      sessionId: null
    })
  })
})
