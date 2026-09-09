// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ModelVisibilityDialog } from './model-visibility-dialog'

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { close: 'Close' },
      modelVisibility: {
        noAuthenticatedProviders: 'No authenticated providers',
        search: 'Search models',
        title: 'Edit models'
      }
    }
  })
}))

vi.mock('@/lib/model-options', () => ({
  modelOptionsQueryKey: () => ['test-model-options'],
  requestModelOptions: async () => ({
    providers: [{ models: ['gemini-2.5-flash'], name: 'Google', slug: 'google' }]
  })
}))

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={client}>
      <ModelVisibilityDialog onOpenChange={() => {}} onOpenProviders={() => {}} open />
    </QueryClientProvider>
  )
}

// #103432: the dialog's provider headings are headings too — accent token,
// while the model rows underneath keep their existing styling.
describe('ModelVisibilityDialog provider heading contrast', () => {
  it('renders provider headings with the accent token, not tertiary', async () => {
    renderDialog()
    await screen.findByText('Google')

    const heading = screen.getByText('Google').closest('button')

    expect(heading?.className).toContain('text-(--ui-accent)')
    expect(heading?.className).not.toContain('text-(--ui-text-tertiary)')
  })

  it('leaves model rows off the accent token', async () => {
    renderDialog()
    await screen.findByText(/Gemini 2\.5 Flash/i)

    const row = screen.getByText(/Gemini 2\.5 Flash/i).closest('label')

    expect(row?.className ?? '').not.toContain('text-(--ui-accent)')
  })
})
