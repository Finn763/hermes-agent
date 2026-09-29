const EMOJI_RE = /(?:[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]|[\u{FE0F}\u{200D}]|[\u{E0020}-\u{E007F}])+/gu

const FENCED_CODE_RE = /```[\s\S]*?(?:```|$)/g
const CODE_BLOCK_SUMMARY = ' code block omitted '
const INLINE_CODE_RE = /`([^`]+)`/g
const MARKDOWN_LINK_RE = /\[([^\]]+)\]\(([^)]+)\)/g
const PARAGRAPH_BREAK_RE = /[ \t]*\n{2,}[ \t]*/g
const PUNCTUATED_PARAGRAPH_BREAK_RE = /([.!?])([*_~`>"'’”)}\]]*)[ \t]*\n{2,}[ \t]*/g
const SOFT_BREAK_RE = /[ \t]*\n[ \t]*/g

const THINKING_PREFIX_RE =
  /^\s*(?:\([^)\n]{1,48}\)\s*)?(?:processing|thinking|reasoning|analyzing|pondering|contemplating|musing|cogitating|ruminating|deliberating|mulling|reflecting|computing|synthesizing|formulating|brainstorming)\.\.\.\s*/i

const URL_RE = /\bhttps?:\/\/\S+/gi

// Identifier-heavy spans (filenames, hashes, paths, model/version IDs) sound
// like corrupted audio when read verbatim (#119207). Same policy as
// tools/tts_text_normalize.py: short placeholder over literal pronunciation.
// ponytail: naive placeholder policy, per-language expansion if it matters.
const FILENAME_RE =
  /(?<![\w.-])[\w][\w.-]*\.(wav|ogg|opus|mp3|flac|m4a|aac|png|jpe?g|gif|webp|mp4|mov|pdf|zip|csv|json|log|txt|bin|py|ts|m?js)\b/gi
const HASH_LABELED_RE = /\bsha[\s_-]*256\s*:\s*[0-9a-fA-F]{6,}(?:\.\.\.)?/gi
const HASH_BARE_RE = /\b[0-9a-fA-F]{32,}\b/g
const PATH_RE = /(?<![\w/.-])([\w][\w.-]*(?:\/[\w][\w.-]*)+)(?![\w/])/g
const VERSION_RE = /(?<![\w.])v?\d+\.\d+\.\d+(?:[-.+][\w.+-]*)?(?![\w.])/g
const DASH_ID_RE = /(?<![\w-])([A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+)(?![\w-])/g

function summarizePathToken(token: string): string {
  // Dates (2026/06/02), and/or, N/A, TCP/IP, $5/month stay speakable.
  if (!/[A-Za-z]/.test(token)) return token
  const slashes = (token.match(/\//g) ?? []).length
  if (slashes >= 2 || token.length >= 12 || /[.\-_]/.test(token)) return 'file path omitted'
  return token
}

function summarizeDashId(token: string): string {
  const seps = (token.match(/[-_]/g) ?? []).length
  if (token.length >= 16 && seps >= 2 && /\d/.test(token)) return 'identifier omitted'
  return token
}

function replaceIdentifiersForSpeech(text: string): string {
  return text
    .replace(HASH_LABELED_RE, 'SHA-256 hash omitted')
    .replace(HASH_BARE_RE, 'hash omitted')
    .replace(PATH_RE, summarizePathToken)
    .replace(FILENAME_RE, (_m, ext: string) => `${ext.toUpperCase()} file`)
    .replace(VERSION_RE, 'version omitted')
    .replace(DASH_ID_RE, summarizeDashId)
}

const MARKDOWN_TABLE_DELIMITER_CELL_RE = /^:?-{3,}:?$/

interface MarkdownTableRow {
  blockquoteDepth: number
  cells: string[]
}

function isUnescapedPipe(row: string, index: number): boolean {
  let backslashes = 0

  for (let cursor = index - 1; cursor >= 0 && row[cursor] === '\\'; cursor -= 1) {
    backslashes += 1
  }

  return backslashes % 2 === 0
}

function splitMarkdownTableCells(row: string): string[] {
  const cells: string[] = []
  let cellStart = 0

  for (let index = 0; index < row.length; index += 1) {
    if (row[index] === '|' && isUnescapedPipe(row, index)) {
      cells.push(row.slice(cellStart, index).trim())
      cellStart = index + 1
    }
  }

  cells.push(row.slice(cellStart).trim())

  return cells
}

function parseMarkdownTableRow(line: string): MarkdownTableRow | null {
  let row = line
  let blockquoteDepth = 0

  while (true) {
    const indentation = row.match(/^[ \t]*/)?.[0] ?? ''

    if (indentation.includes('\t') || indentation.length > 3) {
      return null
    }

    row = row.slice(indentation.length)

    if (!row.startsWith('>')) {
      break
    }

    blockquoteDepth += 1
    row = row.slice(1)

    if (row.startsWith(' ')) {
      row = row.slice(1)
    }
  }

  row = row.trimEnd()

  const pipeIndexes = [...row.matchAll(/\|/g)].map(match => match.index).filter(index => isUnescapedPipe(row, index))

  if (pipeIndexes.length === 0) {
    return null
  }

  const hasLeadingPipe = pipeIndexes[0] === 0
  const hasTrailingPipe = pipeIndexes.at(-1) === row.length - 1

  if (hasLeadingPipe) {
    row = row.slice(1)
  }

  if (hasTrailingPipe) {
    row = row.slice(0, -1)
  }

  const cells = splitMarkdownTableCells(row)

  if (cells.length < 2 && !(hasLeadingPipe && hasTrailingPipe && cells.length === 1)) {
    return null
  }

  return { blockquoteDepth, cells }
}

function stripMarkdownTables(text: string): string {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  const tableLines = new Set<number>()

  let index = 1

  while (index < lines.length) {
    const delimiterRow = parseMarkdownTableRow(lines[index])
    const headerRow = parseMarkdownTableRow(lines[index - 1])

    if (
      !delimiterRow ||
      !headerRow ||
      !delimiterRow.cells.every(cell => MARKDOWN_TABLE_DELIMITER_CELL_RE.test(cell)) ||
      headerRow.cells.length !== delimiterRow.cells.length ||
      headerRow.blockquoteDepth !== delimiterRow.blockquoteDepth
    ) {
      index += 1

      continue
    }

    tableLines.add(index - 1)
    tableLines.add(index)

    let rowIndex = index + 1

    for (; rowIndex < lines.length; rowIndex += 1) {
      const bodyRow = parseMarkdownTableRow(lines[rowIndex])

      if (!bodyRow || bodyRow.blockquoteDepth !== delimiterRow.blockquoteDepth) {
        break
      }

      tableLines.add(rowIndex)
    }

    index = rowIndex
  }

  return lines.filter((_, index) => !tableLines.has(index)).join('\n')
}

function normalizeLineBreaks(text: string): string {
  return text
    .replace(/\r\n?/g, '\n')
    .replace(/(\p{L})-\n(\p{L})/gu, '$1$2')
    .replace(PUNCTUATED_PARAGRAPH_BREAK_RE, '$1$2 ')
    .replace(PARAGRAPH_BREAK_RE, '. ')
    .replace(SOFT_BREAK_RE, ' ')
}

export function sanitizeTextForSpeech(text: string): string {
  // Identifier pass runs before markdown-char stripping so `_`/`#` in
  // filenames and IDs are still visible to it.
  const cleaned = normalizeLineBreaks(stripMarkdownTables(text))
    .replace(FENCED_CODE_RE, CODE_BLOCK_SUMMARY)
    .replace(THINKING_PREFIX_RE, ' ')
    .replace(MARKDOWN_LINK_RE, '$1')
    .replace(INLINE_CODE_RE, '$1')
    .replace(URL_RE, ' link ')

  return replaceIdentifiersForSpeech(cleaned)
    .replace(EMOJI_RE, ' ')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/[*_~>#]/g, '')
    .replace(/^\s*[-+*]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
}
