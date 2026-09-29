import { describe, expect, it } from 'vitest'

import { sanitizeTextForSpeech } from './speech-text'

describe('sanitizeTextForSpeech', () => {
  it('summarizes fenced code blocks instead of reading them literally', () => {
    expect(sanitizeTextForSpeech('Here is code:\n```ts\nconst x = 1\n```\nDone.')).toBe(
      'Here is code: code block omitted Done.'
    )
  })

  it('still keeps normal prose and inline code readable', () => {
    expect(sanitizeTextForSpeech('Use `git status` after the change.')).toBe('Use git status after the change.')
  })

  it('skips markdown table data while preserving surrounding human text', () => {
    const text = `Here is the quick takeaway: the totals remain unchanged.

| Item | Value | Notes |
| --- | ---: | --- |
| Example A | 10 | first row |
| Example B | 20 | second row |

Full detail stays visible on screen.`

    expect(sanitizeTextForSpeech(text)).toBe(
      'Here is the quick takeaway: the totals remain unchanged. Full detail stays visible on screen.'
    )
  })

  it('does not strip prose that merely contains a pipe character', () => {
    const text = 'Use the summary first | keep the table on screen when it matters.'

    expect(sanitizeTextForSpeech(text)).toBe('Use the summary first | keep the table on screen when it matters.')
  })

  it('does not duplicate punctuation across paragraph breaks', () => {
    const text = `First sentence.

Second sentence.`

    expect(sanitizeTextForSpeech(text)).toBe('First sentence. Second sentence.')
  })

  it.each([
    ['markdown emphasis', '**First sentence.**\n\nSecond sentence.', 'First sentence. Second sentence.'],
    ['a closing quote', '“First sentence.”\n\nSecond sentence.', '“First sentence.” Second sentence.'],
    ['a closing parenthesis', '(First sentence.)\n\nSecond sentence.', '(First sentence.) Second sentence.']
  ])('does not duplicate punctuation after %s', (_label, text, expected) => {
    expect(sanitizeTextForSpeech(text)).toBe(expected)
  })

  it('skips markdown tables without leading and trailing pipes', () => {
    const text = `Main takeaway: total is unchanged.

Item | Value
--- | ---:
Example A | 10
Example B | 20

Done.`

    expect(sanitizeTextForSpeech(text)).toBe('Main takeaway: total is unchanged. Done.')
  })

  it('skips markdown tables nested inside blockquotes', () => {
    const text = `Before the table.

> | Item | Value |
> | --- | ---: |
> | Example A | 10 |
> | Example B | 20 |

After the table.`

    expect(sanitizeTextForSpeech(text)).toBe('Before the table. After the table.')
  })

  it('allows marker padding plus three spaces in blockquoted tables', () => {
    const text = `Before the table.

>    | Item | Value |
>    | --- | ---: |
>    | Example A | 10 |

After the table.`

    expect(sanitizeTextForSpeech(text)).toBe('Before the table. After the table.')
  })

  it('skips explicit single-column markdown tables', () => {
    const text = `Before the table.

| Item |
| --- |
| Example A |

After the table.`

    expect(sanitizeTextForSpeech(text)).toBe('Before the table. After the table.')
  })

  it('preserves rows outside a table blockquote', () => {
    const text = `> | Item | Value |
> | --- | ---: |
> | Example A | 10 |
Outside | prose`

    expect(sanitizeTextForSpeech(text)).toBe('Outside | prose')
  })

  it('preserves malformed tables with mismatched column counts', () => {
    const text = `Heading | Detail
--- | --- | ---
Keep this prose.`

    expect(sanitizeTextForSpeech(text)).toContain('Heading | Detail')
  })

  it('skips GFM body rows whose cell counts differ from the header', () => {
    const text = `Before the table.

| Item | Value |
| --- | ---: |
| Example A |
| Example B | 20 | ignored |

After the table.`

    expect(sanitizeTextForSpeech(text)).toBe('Before the table. After the table.')
  })

  it('skips tables containing escaped pipe characters', () => {
    const text = `Before the table.

| Item \\| detail | Value |
| --- | ---: |
| Example A | 10 |

After the table.`

    expect(sanitizeTextForSpeech(text)).toBe('Before the table. After the table.')
  })

  it('preserves indented code that resembles a table', () => {
    const text = `    Item | Value
    --- | ---
    Example A | 10`

    expect(sanitizeTextForSpeech(text)).toContain('Item | Value')
  })

  it('rewrites filenames, hashes, paths, and IDs instead of reading them raw', () => {
    expect(sanitizeTextForSpeech('I generated peyton-sample-20260922.wav today.')).toBe(
      'I generated WAV file today.'
    )
    expect(sanitizeTextForSpeech('sha256: abc123def456789012345678901234567890 ok')).toBe(
      'SHA-256 hash omitted ok'
    )
    expect(sanitizeTextForSpeech('see models/gpt-4o/checkpoint-20260922.bin today')).toBe(
      'see file path omitted today'
    )
    expect(sanitizeTextForSpeech('released model-xyz-20260922-alpha build')).toBe(
      'released identifier omitted build'
    )
    // Speakable text stays intact: prose, dates, and/or, rates.
    expect(sanitizeTextForSpeech('Use git status after the change.')).toBe(
      'Use git status after the change.'
    )
    expect(sanitizeTextForSpeech('choose and/or option, due 2026/06/02 ok')).toBe(
      'choose and/or option, due 2026/06/02 ok'
    )
  })

  it('keeps addresses and slash prose speakable without swallowing hyphenated prose', () => {
    // Review follow-up: IPv4 must not read as "version omitted" (#119207).
    expect(sanitizeTextForSpeech('Connect to 127.0.0.1:8080 first')).toBe('Connect to 127.0.0.1:8080 first')
    expect(sanitizeTextForSpeech('It resolved to 192.168.1.100 today')).toBe('It resolved to 192.168.1.100 today')
    // Slash prose stays speakable whatever its length; a real path still drops.
    expect(sanitizeTextForSpeech('check the input/output port')).toBe('check the input/output port')
    expect(sanitizeTextForSpeech('stack uses TCP/IP here, N/A there')).toBe('stack uses TCP/IP here, N/A there')
    expect(sanitizeTextForSpeech('see src/lib/speech-text.ts today')).toBe('see file path omitted today')
    // A version's optional suffix must not swallow hyphenated prose.
    expect(sanitizeTextForSpeech('Use version 2.0.0-or-later for compatibility')).toContain('or-later')
    expect(sanitizeTextForSpeech('Upgrade to 1.2.3 now')).toBe('Upgrade to version omitted now')
  })
})
