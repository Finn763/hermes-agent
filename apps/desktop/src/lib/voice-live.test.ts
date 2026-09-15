// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({ profileScoped: () => ({}) }))
vi.mock('@/hermes', () => ({
  hermesApi: vi.fn(async () => ({
    ok: true,
    session: { id: 'live-1' },
    transport: { sdp: 'answer-sdp', type: 'answer' }
  }))
}))

import { DEFAULT_IDLE_HANGUP_SECONDS, IDLE_HANGUP_REASON, LiveIdleWatchdog, VoiceLiveSession } from './voice-live'

/** Minimal stand-in for the `oai-events` channel: records what the desktop sent. */
class FakeDataChannel {
  readyState = 'open'
  sent: Array<Record<string, unknown>> = []
  private listeners = new Map<string, Array<(event: { data?: string }) => void>>()

  addEventListener(type: string, listener: (event: { data?: string }) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close() {
    this.readyState = 'closed'
  }

  emit(type: string, event: { data?: string }) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }

  send(data: string) {
    this.sent.push(JSON.parse(data) as Record<string, unknown>)
  }
}

class FakePeerConnection {
  static created: FakePeerConnection[] = []
  channel = new FakeDataChannel()
  connectionState = 'connected'
  iceGatheringState = 'complete'
  localDescription = { sdp: 'offer-sdp', type: 'offer' }

  constructor() {
    FakePeerConnection.created.push(this)
  }

  addEventListener() {}
  addTrack() {}
  close() {}
  createDataChannel() {
    return this.channel
  }
  async createOffer() {
    return { sdp: 'offer-sdp', type: 'offer' }
  }
  async setLocalDescription() {}
  async setRemoteDescription() {}
}

beforeEach(() => {
  FakePeerConnection.created = []
  vi.useFakeTimers()
  vi.stubGlobal('RTCPeerConnection', FakePeerConnection)
  vi.stubGlobal(
    'Audio',
    class {
      autoplay = false
      srcObject: unknown = null
      pause = () => {}
      play = () => Promise.resolve()
    }
  )
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async () => {
        const track = { enabled: true, stop: vi.fn() }

        return { getAudioTracks: () => [track], getTracks: () => [track] }
      })
    }
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Real session over the fake transport, with an injectable clock. */
async function startSession(idleHangupSeconds: number) {
  const clock = { now: 1_000_000 }
  const handlers = { onClosed: vi.fn(), onDelegation: vi.fn(), onError: vi.fn() }
  const session = new VoiceLiveSession(handlers, { idleHangupSeconds, now: () => clock.now })

  await session.start([])

  return { channel: FakePeerConnection.created.at(-1)!.channel, clock, handlers, session }
}

/**
 * Run the session's own tick for `ms` and then move the injected clock forward:
 * ticks see the time from before this call, so an expired deadline is noticed on
 * the tick after the crossing — exactly like the real poll.
 */
async function pass(harness: { clock: { now: number } }, ms: number) {
  await vi.advanceTimersByTimeAsync(ms)
  harness.clock.now += ms
}

const transcript = (type: 'session.input_transcript.delta' | 'session.output_transcript.delta', text: string) => ({
  data: JSON.stringify({ delta: text, type })
})

/** The vendor answers our `session.close` with `session.closed` (its own reason). */
const ackClose = (harness: { channel: FakeDataChannel }, usageSeconds: null | number = null) =>
  harness.channel.emit('message', {
    data: JSON.stringify({
      reason: 'closed',
      type: 'session.closed',
      usage: usageSeconds == null ? undefined : { seconds: usageSeconds }
    })
  })

describe('LiveIdleWatchdog', () => {
  it('expires only once the deadline has passed', () => {
    let now = 0
    const watchdog = new LiveIdleWatchdog(300_000, () => now)

    now = 299_999
    expect(watchdog.expired()).toBe(false)

    now = 300_000
    expect(watchdog.expired()).toBe(true)
  })

  it('is disabled at 0 — never expires', () => {
    let now = 0
    const watchdog = new LiveIdleWatchdog(0, () => now)

    expect(watchdog.enabled).toBe(false)

    now = 86_400_000
    expect(watchdog.expired()).toBe(false)
  })

  it('restarts the clock on activity', () => {
    let now = 0
    const watchdog = new LiveIdleWatchdog(60_000, () => now)

    now = 59_000
    watchdog.note()

    now = 118_999
    expect(watchdog.expired()).toBe(false)

    now = 119_000
    expect(watchdog.expired()).toBe(true)
  })

  it('takes the configured threshold', () => {
    let now = 0
    const watchdog = new LiveIdleWatchdog(45_000, () => now)

    now = 44_999
    expect(watchdog.expired()).toBe(false)

    now = 45_000
    expect(watchdog.expired()).toBe(true)
  })
})

describe('GPT-Live idle hangup', () => {
  it('hangs up a silent call with session.close and reports the idle reason', async () => {
    const harness = await startSession(DEFAULT_IDLE_HANGUP_SECONDS)

    await pass(harness, DEFAULT_IDLE_HANGUP_SECONDS * 1_000)
    expect(harness.channel.sent).toEqual([])
    expect(harness.handlers.onClosed).not.toHaveBeenCalled()

    await pass(harness, 1_000)

    // Nothing but the one close event: the live connection, not the chat.
    expect(harness.channel.sent).toEqual([{ type: 'session.close' }])
    expect(harness.handlers.onClosed).not.toHaveBeenCalled()

    ackClose(harness)

    // Reported as OUR idle reason, not the vendor's echoed "closed".
    expect(harness.handlers.onClosed).toHaveBeenCalledWith(IDLE_HANGUP_REASON, null)
    expect(harness.handlers.onError).not.toHaveBeenCalled()
    expect(harness.handlers.onDelegation).not.toHaveBeenCalled()
  })

  it('reports the idle reason even when the vendor never acknowledges the close', async () => {
    const harness = await startSession(60)

    await pass(harness, 60_000)
    await pass(harness, 1_000)
    expect(harness.channel.sent).toEqual([{ type: 'session.close' }])
    expect(harness.handlers.onClosed).not.toHaveBeenCalled()

    await pass(harness, 15_000)
    expect(harness.handlers.onClosed).toHaveBeenCalledWith(IDLE_HANGUP_REASON, null)
  })

  it('stays up while the user or the assistant is speaking', async () => {
    const harness = await startSession(60)

    await pass(harness, 40_000)
    harness.channel.emit('message', transcript('session.input_transcript.delta', 'still here'))
    await pass(harness, 15_000)
    harness.channel.emit('message', transcript('session.output_transcript.delta', 'yes?'))

    await pass(harness, 60_000)
    expect(harness.channel.sent).toEqual([])

    // Resets pushed the deadline out: it fires 115s in, not 60s.
    await pass(harness, 1_000)
    expect(harness.channel.sent).toEqual([{ type: 'session.close' }])
    ackClose(harness)
    expect(harness.handlers.onClosed).toHaveBeenCalledWith(IDLE_HANGUP_REASON, null)
  })

  it('stays up while a Hermes turn is in flight', async () => {
    const harness = await startSession(60)

    for (let round = 0; round < 3; round += 1) {
      await pass(harness, 50_000)
      harness.session.noteActivity()
    }

    await pass(harness, 1_000)
    expect(harness.channel.sent).toEqual([])
    expect(harness.handlers.onClosed).not.toHaveBeenCalled()
  })

  it('counts an outbound commentary append as activity', async () => {
    const harness = await startSession(60)

    await pass(harness, 50_000)
    harness.session.speak(null, 'Here is what I found.')

    await pass(harness, 50_000)
    await pass(harness, 1_000)
    expect(harness.handlers.onClosed).not.toHaveBeenCalled()
  })

  it('never hangs up when the deadline is 0', async () => {
    const harness = await startSession(0)

    await pass(harness, 3_600_000)
    await pass(harness, 1_000)

    expect(harness.channel.sent).toEqual([])
    expect(harness.handlers.onClosed).not.toHaveBeenCalled()
  })

  it('honours a configured deadline', async () => {
    const harness = await startSession(45)

    await pass(harness, 44_000)
    await pass(harness, 1_000)
    // 44s in: still up, and long before the 300s default would have fired.
    expect(harness.channel.sent).toEqual([])

    await pass(harness, 1_000)
    expect(harness.channel.sent).toEqual([{ type: 'session.close' }])
    ackClose(harness)
    expect(harness.handlers.onClosed).toHaveBeenCalledWith(IDLE_HANGUP_REASON, null)
  })

  it('still reports the user’s own stop as close_requested, not the vendor echo', async () => {
    const harness = await startSession(DEFAULT_IDLE_HANGUP_SECONDS)

    harness.session.close()
    expect(harness.channel.sent).toEqual([{ type: 'session.close' }])

    ackClose(harness)
    expect(harness.handlers.onClosed).toHaveBeenCalledWith('close_requested', null)
  })
})
