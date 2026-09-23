import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The consent dialog has its own test file and needs a QueryClientProvider;
// this test exercises the pop-out content handoff, not the prompt.
vi.mock('./right-rail/real-profile-consent-dialog', () => ({
  RealProfileConsentDialog: () => null
}))

// A popped-out Browser window is a FRESH renderer: no in-memory atoms cross
// the window boundary, and no session ever pushes a rail scope there. All it
// knows arrives via shared persisted tabs plus `?win=browser&tab=` in its own
// URL. Each test therefore resets the module registry (fresh atoms, cold
// caches — exactly what a new renderer process boots with), seeds storage the
// way the main window left it, sets the pop-out query string, and only then
// imports the shell. Regression tests for the black pop-out window (shell
// spawns, no URL bar, content never paints).
const TABS_KEY = 'hermes.desktop.previewTabs.v2'

// Cold module graph, not test logic: the shell pulls in the whole app.
vi.setConfig({ testTimeout: 90000 })

function tabRow(id: string, url: string) {
  return { id, target: { kind: 'url', label: 'kanban', source: url, url } }
}

beforeEach(() => {
  vi.resetModules()
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  window.history.replaceState({}, '', '/')
})

async function renderFreshPopout(tabId: string) {
  window.history.replaceState({}, '', `/?win=browser&tab=${encodeURIComponent(tabId)}#/`)
  const { BrowserPopoutShell } = await import('./browser-popout-shell')

  let rendered!: ReturnType<typeof render>
  await act(async () => {
    rendered = render(<BrowserPopoutShell />)
  })

  return rendered
}

async function expectGuestWithUrl(rendered: ReturnType<typeof render>, url: string) {
  // The tab arrives via a store update after mount; wait for the re-render.
  // Generous timeout: the first test in this file pays the cold module-graph
  // import (~14s) and every poll must survive it.
  await waitFor(
    () => {
      expect(rendered.container.querySelector('webview')?.getAttribute('src')).toBe(url)
    },
    { timeout: 30000 }
  )

  // Identity: the pop-out bar offers pop-in for this tab (icon-only glyph with
  // an accessible name, so assert the aria-label, not text content).
  expect(rendered.container.querySelector('[aria-label="Pop in"]')).not.toBeNull()

  // Address bar tracks the handed-off URL.
  expect((rendered.container.querySelector('input') as HTMLInputElement | null)?.value).toContain(
    '127.0.0.1'
  )
}

describe('browser pop-out content handoff', () => {
  it('hands the persisted tab URL to a guest (pre-scoping single-array storage)', async () => {
    const id = 'url:browser-legacy-1'
    const url = 'http://127.0.0.1:9119/kanban'
    window.localStorage.setItem(TABS_KEY, JSON.stringify([tabRow(id, url)]))

    const rendered = await renderFreshPopout(id)
    await expectGuestWithUrl(rendered, url)
  })

  it('hands the persisted tab URL to a guest (profile-bucketed storage)', async () => {
    const id = 'url:browser-bucket-1'
    const url = 'http://127.0.0.1:9119/kanban'
    window.localStorage.setItem(TABS_KEY, JSON.stringify({ local: [tabRow(id, url)] }))

    const rendered = await renderFreshPopout(id)
    await expectGuestWithUrl(rendered, url)
  })

  it('leaves an unknown tab blank rather than adopting a stranger', async () => {
    const url = 'http://127.0.0.1:9119/kanban'
    window.localStorage.setItem(TABS_KEY, JSON.stringify({ local: [tabRow('url:browser-known-1', url)] }))

    const rendered = await renderFreshPopout('url:browser-nope')

    expect(rendered.container.querySelector('webview')).toBeNull()
    expect(rendered.container.textContent).not.toContain('Pop in')
  })
})
