/**
 * Roster sections are the SAME on every desktop build.
 *
 * #114355 reported the Bot Mode roster sections — the `+` menu's "New section",
 * a row's "Move to section → New section…", and the grouped headings — as
 * present on macOS and missing on Linux at the same version. There is no
 * platform branch to find: the entries are unconditional
 * (`roster-pane-toolbar.tsx:113-117` `New section`, `bot-row.tsx:428-453` /
 * `bot-row.tsx:611-636` the `Move to section` submenu, `roster-pane-sections.tsx`
 * + `user-sections.ts` the headings), and `src/plugins/hermes-bots/` never
 * consults `process.platform` / `isMacPlatform()` / a feature flag. The Linux
 * report is therefore a build that predates the feature, not a gated UI — and
 * the ONE thing a second client genuinely cannot see is data, not UI (see the
 * note at the bottom of this file).
 *
 * This file is the guard for that conclusion: every surface is rendered under a
 * macOS, Linux and Windows platform ident and must produce the SAME DOM. Add a
 * platform branch to any of them and these cases fail.
 */

import type * as HermesSdk from '@hermes/plugin-sdk'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BotRow } from './bot-row'
import { useBots } from './i18n'
import { translateBots } from './i18n-test-helper'
import { rosterSectionRenderers } from './roster-pane-sections'
import { renderRosterToolbar } from './roster-pane-toolbar'
import type { BotMeta, RosterRow } from './types'
import { $botSections, normalizeBotSections } from './user-sections'

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()

  // The bundle normally lands via `ctx.i18n.register` at plugin load, so
  // without this every label in these surfaces renders empty.
  return { ...sdk, usePluginI18n: () => translateBots }
})

vi.mock('./canonical-chat', () => ({
  ensureBotMetadata: vi.fn().mockResolvedValue({}),
  notifyBotOpenFailure: vi.fn(),
  openBotCanonicalChat: vi.fn(),
  prepareBotSource: vi.fn(),
  PROFILE_SESSION_LIST_LIMIT: 200
}))

vi.mock('./roster-actions', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  openRosterBot: vi.fn()
}))

const noop = () => undefined

/** The three desktop builds the roster must behave identically on. */
const PLATFORMS = [
  ['macOS', 'MacIntel', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'],
  ['Linux', 'Linux x86_64', 'Mozilla/5.0 (X11; Linux x86_64)'],
  ['Windows', 'Win32', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)']
] as const

const realPlatform = { platform: navigator.platform, userAgent: navigator.userAgent }

function pinPlatform(platform: string, userAgent: string) {
  Object.defineProperty(window.navigator, 'platform', { configurable: true, value: platform })
  Object.defineProperty(window.navigator, 'userAgent', { configurable: true, value: userAgent })
}

function Toolbar({ onCreateNewSection }: { onCreateNewSection: (value: unknown) => void }) {
  return (
    <>
      {renderRosterToolbar({
        b: useBots(),
        activeFilterCount: 0,
        activeSourceRoster: [{ name: 'alpha' }],
        activityFilter: 'all',
        activityToasts: true,
        gatewayFilter: 'all',
        gatewayOptions: [],
        query: '',
        roster: [{ name: 'alpha' }, { name: 'beta' }],
        rowKindFilter: 'all',
        setActivityFilter: noop,
        setCreateOpen: noop,
        setGatewayFilter: noop,
        setGroupCreateOpen: noop,
        setQuery: noop,
        setRowKindFilter: noop,
        setSectionDialog: onCreateNewSection,
        showRosterFilters: true,
        showRosterSearch: true,
        showRosterTools: true
      })}
    </>
  )
}

/** The + menu as a reader meets it: open it, then read the items. */
async function openNewMenu(onCreateNewSection: (value: unknown) => void) {
  render(<Toolbar onCreateNewSection={onCreateNewSection} />)
  fireEvent.pointerDown(screen.getByRole('button', { name: 'New bot or group chat' }))

  return screen.findByRole('menu')
}

beforeEach(() => {
  $botSections.set([])
})

afterEach(() => {
  pinPlatform(realPlatform.platform, realPlatform.userAgent)
})

describe.each(PLATFORMS)('the Bots pane + menu on %s', (_name, platform, userAgent) => {
  it('offers New section beside New bot and New group chat', async () => {
    pinPlatform(platform, userAgent)

    const menu = await openNewMenu(noop)

    expect(within(menu).getByText('New bot')).toBeTruthy()
    expect(within(menu).getByText('New group chat')).toBeTruthy()
    expect(within(menu).getByText('New section')).toBeTruthy()
  })

  it('opens the New section dialog from that entry', async () => {
    pinPlatform(platform, userAgent)

    const setSectionDialog = vi.fn()
    const menu = await openNewMenu(setSectionDialog)

    fireEvent.click(within(menu).getByText('New section'))

    expect(setSectionDialog.mock.calls).toEqual([[{ mode: 'create' }]])
  })
})

describe.each(PLATFORMS)('a bot row on %s', (_name, platform, userAgent) => {
  it('carries Move to section → New section…', async () => {
    pinPlatform(platform, userAgent)

    render(
      <BotRow bot={{ name: 'alpha' } as RosterRow} onDelete={noop} onEdit={noop} onGroup={noop} onNewSection={noop} />
    )

    fireEvent.contextMenu(screen.getByRole('button'))
    const menu = await screen.findByRole('menu')

    // The submenu trigger IS the entry #114355 reported missing: it is a plain
    // unconditional <ContextMenuSub> beside Delete.
    expect(within(menu).getByText('Move to section')).toBeTruthy()
  })
})

describe.each(PLATFORMS)('the roster grouping on %s', (_name, platform, userAgent) => {
  it('draws a heading per filed section and Unassigned last', () => {
    pinPlatform(platform, userAgent)

    const sections = normalizeBotSections([{ id: 'sec-clients', name: 'Clients' }])
    const allMeta: Record<string, BotMeta> = { alpha: { sectionId: 'sec-clients' } }

    $botSections.set(sections)

    function Grouped() {
      const b = useBots()

      const { renderUserSections } = rosterSectionRenderers({
        allMeta,
        b,
        dragging: null,
        groupRooms: {},
        renderBotRow: row => <div key={row.name}>{row.name}</div>,
        renderGroupRow: () => null,
        roster: [{ name: 'alpha' }, { name: 'zeta' }],
        rosterSectionCollapsed: () => false,
        setSectionDialog: noop,
        sortedGroupRows: [],
        toggleRosterSection: noop,
        userSections: sections
      })

      return <div data-slot="bots-roster">{renderUserSections([{ bot: { name: 'alpha' } }, { bot: { name: 'zeta' } }])}</div>
    }

    const { container } = render(<Grouped />)

    expect(screen.getByText('Clients')).toBeTruthy()
    expect(screen.getByText('Unassigned')).toBeTruthy()

    // The filed bot sits in the Clients block; the unfiled one in Unassigned.
    const clients = container.querySelector('[data-section-id="sec-clients"]')?.closest('[data-slot="bots-section"]')
    const blocks = [...container.querySelectorAll('[data-slot="bots-section"]')]

    expect(clients?.textContent).toContain('alpha')
    expect(clients?.textContent).not.toContain('zeta')
    expect(blocks.at(-1)?.textContent).toContain('zeta')
    expect(blocks.at(-1)?.textContent).toContain('Unassigned')
  })
})

/**
 * NOT covered here, because it is a data path rather than a platform branch:
 * the section RECORDS (`bot-sections-v1`, `user-sections.ts:40`) are loaded from
 * local plugin storage (`loadBotSections`, `user-sections.ts:90-96`), while the
 * membership rides each bot's profile `ui_meta` (`sectionId`) and therefore
 * reaches every machine. A second client — the Linux box on the shared gateway
 * in #114355 — holds `sectionId`s it has no name for, so `renderUserSections`
 * short-circuits to the flat list (`roster-pane-sections.tsx:75-79`) and no
 * heading is drawn. That is the flat-list half of the report, and it needs the
 * section list published (or the name carried with the membership) before any
 * client can draw another machine's sections — a separate change from this
 * guard, deliberately not faked here.
 */
