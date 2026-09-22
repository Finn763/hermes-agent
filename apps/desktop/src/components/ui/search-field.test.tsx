import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { SearchField } from './search-field'

function renderField(value: string) {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <div style={{ width: 320 }}>
        <SearchField onChange={() => {}} placeholder="Search" value={value} />
      </div>
    </I18nProvider>
  )
}

describe('SearchField clear button placement', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('input fills the row width instead of sizing to its content', () => {
    const { container } = renderField('x')
    const input = container.querySelector('input')
    expect(input).toBeTruthy()
    // flex-1: input takes the remaining row width, pushing clear to the edge.
    expect(input!.className).toMatch(/(^|\s)flex-1(\s|$)/)
    // field-sizing:content shrinks the input to the typed text, dragging X along.
    expect(input!.className).not.toContain('field-sizing')
  })

  it('clear button sits last in the row (right edge), not after the text', () => {
    const { container } = renderField('x')
    const clear = screen.getByRole('button', { name: 'Clear search' })
    const row = clear.closest('div[class*="inline-flex"]')
    expect(row).toBeTruthy()
    expect(row!.lastElementChild?.contains(clear)).toBe(true)
  })
})
