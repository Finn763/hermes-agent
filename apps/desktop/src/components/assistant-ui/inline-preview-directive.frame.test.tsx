// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The frame renders standalone in the transcript: the session view provides the
// cwd, the fs bridge returns the HTML. Everything else is inert here.
vi.mock('@/app/chat/session-view', async () => {
  const { atom } = await import('nanostores')
  const $cwd = atom('/tmp/ws')

  return { useSessionView: () => ({ $cwd }) }
})

vi.mock('@/components/assistant-ui/embeds/use-is-dark', () => ({ useIsDark: () => true }))

vi.mock('@/lib/desktop-fs', () => ({
  readDesktopFileText: vi.fn(async () => ({
    binary: false,
    text: '<html><body><table><tr><td>wide</td></tr></table></body></html>'
  }))
}))

vi.mock('@/lib/local-preview', () => ({
  localPreviewTarget: (file: string) => ({ path: `/tmp/ws/${file}` })
}))

vi.mock('@/app/chat/composer/focus', () => ({ requestComposerSubmit: vi.fn() }))

vi.mock('@/components/chat/preview-attachment', () => ({ PreviewAttachment: () => null }))

import { InlinePreviewDirective } from './inline-preview-directive'

afterEach(cleanup)

async function renderFrame(attrs: Record<string, string>) {
  const { container } = render(<InlinePreviewDirective attrs={attrs} streaming={false} />)

  const frame = await waitFor(() => {
    const el = container.querySelector<HTMLElement>('span.overflow-x-auto')

    expect(el).toBeTruthy()

    return el as HTMLElement
  })

  return { container, frame }
}

describe('InlinePreviewDirective frame width cap', () => {
  it('threads a raised max-width into the rendered frame (not a static class)', async () => {
    const { frame } = await renderFrame({ file: 'widget.html', 'max-width': '2560' })

    // Regression for the inert-cap bug: the resolved value must reach the DOM.
    expect(frame.style.maxWidth).toBe('2560px')
    expect(frame.className).not.toContain('max-w-160')
  })

  it('renders the shipped default when no attribute or override is set', async () => {
    const { frame } = await renderFrame({ file: 'widget.html' })

    expect(frame.style.maxWidth).toBe('640px')
  })

  it('keeps the intrinsic content width inside the capped window (scroll affordance)', async () => {
    const { container, frame } = await renderFrame({ file: 'widget.html' })

    const iframe = container.querySelector('iframe')
    const token = /var t="([^"]+)"/.exec(iframe?.getAttribute('srcdoc') ?? '')?.[1]

    expect(token).toBeTruthy()

    fireEvent(
      window,
      new MessageEvent('message', {
        data: { height: 400, token, type: 'hermes-inline-preview-size', width: 1200 }
      })
    )

    // 1200px of content in a 640px window: the content is not clamped to the
    // window, so the wrapper scrolls and nothing is silently clipped.
    await waitFor(() => {
      const el = frame.querySelector<HTMLElement>('span.relative')

      expect(el?.style.width).toBe('1200px')
    })
    expect(frame.style.maxWidth).toBe('640px')
  })
})
