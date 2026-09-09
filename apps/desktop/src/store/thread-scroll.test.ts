import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  $threadJumpButtonVisible,
  $threadMessagesBelow,
  $threadScrolledUp,
  onScrollToBottomRequest,
  publishThreadAtBottom,
  publishThreadMessagesBelow,
  requestScrollToBottom,
  resetPublishedThreadScroll,
  resetThreadScroll,
  setThreadAtBottom,
  threadJumpButtonVisibleStore,
  threadMessagesBelowStore,
  threadScrolledUpStore
} from './thread-scroll'

afterEach(() => {
  resetThreadScroll()
  resetThreadScroll('session-a')
  resetThreadScroll('session-b')
})

describe('publishThreadAtBottom', () => {
  it('lets the visible pane flash the jump pill when the thread leaves the bottom', () => {
    publishThreadAtBottom(false, { paneVisible: true })

    expect($threadJumpButtonVisible.get()).toBe(true)
    expect($threadScrolledUp.get()).toBe(true)
  })

  it('ignores stick-to-bottom misses from a hidden keep-alive pane', () => {
    setThreadAtBottom(true)

    publishThreadAtBottom(false, { paneVisible: false })

    expect($threadJumpButtonVisible.get()).toBe(false)
    expect($threadScrolledUp.get()).toBe(false)
  })

  it("keeps the visible pane's scrolled-up chrome when a hidden pane publishes", () => {
    publishThreadAtBottom(false, { paneVisible: true })

    publishThreadAtBottom(true, { paneVisible: false })

    expect($threadJumpButtonVisible.get()).toBe(true)
    expect($threadScrolledUp.get()).toBe(true)
  })
})

describe('resetPublishedThreadScroll', () => {
  it('clears the jump pill when the visible pane unmounts', () => {
    setThreadAtBottom(false)

    resetPublishedThreadScroll({ paneVisible: true })

    expect($threadJumpButtonVisible.get()).toBe(false)
    expect($threadScrolledUp.get()).toBe(false)
  })

  it('does not clear the visible pane when a hidden list unmounts', () => {
    setThreadAtBottom(false)

    resetPublishedThreadScroll({ paneVisible: false })

    expect($threadJumpButtonVisible.get()).toBe(true)
    expect($threadScrolledUp.get()).toBe(true)
  })
})

describe('requestScrollToBottom', () => {
  it('routes a scroll request only to its session', () => {
    const sessionA = vi.fn()
    const sessionB = vi.fn()
    const stopA = onScrollToBottomRequest(sessionA, 'session-a')
    const stopB = onScrollToBottomRequest(sessionB, 'session-b')

    requestScrollToBottom('session-b')

    expect(sessionA).not.toHaveBeenCalled()
    expect(sessionB).toHaveBeenCalledOnce()
    stopA()
    stopB()
  })

  it("does not let a late unmount clear a newer session's handler", () => {
    const first = vi.fn()
    const second = vi.fn()
    const stopFirst = onScrollToBottomRequest(first, 'session-a')
    const stopSecond = onScrollToBottomRequest(second, 'session-a')

    stopFirst()
    requestScrollToBottom('session-a')

    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledOnce()
    stopSecond()
  })
})

describe('split-pane scroll isolation (#103586)', () => {
  it('shows the jump button only in the pane that scrolled up', () => {
    publishThreadAtBottom(false, { paneVisible: true }, 'session-a')

    expect(threadJumpButtonVisibleStore('session-a').get()).toBe(true)
    expect(threadScrolledUpStore('session-a').get()).toBe(true)
    expect(threadJumpButtonVisibleStore('session-b').get()).toBe(false)
    expect(threadScrolledUpStore('session-b').get()).toBe(false)
  })

  it('keeps the below-count per session', () => {
    publishThreadMessagesBelow(4, { paneVisible: true }, 'session-a')

    expect(threadMessagesBelowStore('session-a').get()).toBe(4)
    expect(threadMessagesBelowStore('session-b').get()).toBe(0)
  })

  it("an unmount resets only its own pane's session", () => {
    publishThreadAtBottom(false, { paneVisible: true }, 'session-a')
    publishThreadAtBottom(false, { paneVisible: true }, 'session-b')

    resetPublishedThreadScroll({ paneVisible: true }, 'session-a')

    expect(threadJumpButtonVisibleStore('session-a').get()).toBe(false)
    expect(threadJumpButtonVisibleStore('session-b').get()).toBe(true)
  })

  it('keeps the unscoped single-pane behavior on the default slot', () => {
    publishThreadAtBottom(false, { paneVisible: true })

    expect($threadJumpButtonVisible.get()).toBe(true)
    expect($threadScrolledUp.get()).toBe(true)

    publishThreadMessagesBelow(3, { paneVisible: true })

    expect($threadMessagesBelow.get()).toBe(3)
  })
})
