import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const src = readFileSync(new URL('../app/useSessionLifecycle.ts', import.meta.url), 'utf8')

const blockBetween = (start: string, end: string) => {
  const from = src.indexOf(start)
  const to = src.indexOf(end, from)
  if (from < 0 || to < 0 || to <= from) {
    throw new Error(`test setup: could not slice ${start}..${end}`)
  }
  return src.slice(from, to)
}

// Regression test for #121121: resuming a history session must not close the
// previous session. session.close tears down the server session and hard
// interrupts its running delegate_task subagents. Switching to a live session
// (activateLiveSession) only attaches and never closes; resume must match.
describe('resume keeps the previous session alive (#121121)', () => {
  it('resumeById never closes the previous session', () => {
    const resume = blockBetween('const resumeById', 'const guardBusySessionSwitch')
    expect(resume).toContain('session.resume')
    expect(resume).not.toMatch(/closeSession\s*\(/)
    expect(resume).not.toContain('session.close')
  })

  it('activateLiveSession never closes siblings (parity)', () => {
    const activate = blockBetween('const activateLiveSession', 'const resumeById')
    expect(activate).toContain('session.activate')
    expect(activate).not.toMatch(/closeSession\s*\(/)
  })

  it('keeps an explicit close path for user-initiated close', () => {
    // startNewSession(close on replace) and Ctrl+D still need session.close.
    expect(src).toContain("'session.close'")
  })
})
