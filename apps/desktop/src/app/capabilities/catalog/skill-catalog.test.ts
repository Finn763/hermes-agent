import { describe, expect, it } from 'vitest'

import type { SkillInfo } from '@/types/hermes'

import { type CatalogEntry, parseCatalog } from './catalog-data'
import {
  isSkillEntryInstalled,
  isSkillInstallBlocked,
  type SkillCatalogInstallIndex
} from './skill-catalog'

const HUB_IDENTIFIER = 'clawhub/hoyaryyj/kepano-defuddle'

function setupIndex() {
  const hubSkill: SkillInfo = {
    category: 'dev',
    description: 'defuddle workflow',
    enabled: true,
    name: 'defuddle',
    provenance: 'hub'
  }

  // Installed row, shaped exactly like the memo's localEntries mapping.
  const installed = {
    ...parseCatalog('skills', [
      { name: 'defuddle', description: 'defuddle workflow', category: 'dev', source: 'hub' }
    ])[0],
    id: 'installed:defuddle',
    installIdentifier: null
  } satisfies CatalogEntry

  const skillsById = new Map([[installed.id, hubSkill]])
  const installedByIdentifier = new Map([[HUB_IDENTIFIER, installed]])

  const index: SkillCatalogInstallIndex = {
    skillsById,
    // Mirrors the memo's skillsByName: the profile's local skills keyed by name.
    skillsByName: new Map([['defuddle', hubSkill]]),
    // Mirrors the memo's matchInstalled: exact identifier hits only.
    matchInstalled: entry => installedByIdentifier.get(entry.installIdentifier ?? entry.identifier),
    officialFor: () => undefined,
    installedIdentifiers: new Set([HUB_IDENTIFIER])
  }

  // Same-name feed rows from another source: the #126991 ghost rows.
  const lookalikes = parseCatalog('skills', [
    {
      name: 'defuddle',
      description: 'lookalike one',
      category: 'dev',
      source: 'skills.sh',
      identifier: 'panniantong/defuddle'
    },
    {
      name: 'defuddle',
      description: 'lookalike two',
      category: 'dev',
      source: 'skills.sh',
      identifier: 'someone-else/defuddle'
    }
  ])

  const unrelated = parseCatalog('skills', [
    {
      name: 'other-skill',
      description: 'unrelated',
      category: 'dev',
      source: 'skills.sh',
      identifier: 'someone-else/other-skill'
    }
  ])[0]

  return { installed, lookalikes, unrelated, index }
}

describe('isSkillEntryInstalled', () => {
  it('keeps the true installed row installed', () => {
    const { installed, index } = setupIndex()

    expect(isSkillEntryInstalled(installed, index)).toBe(true)
  })

  it('leaves same-name feed rows from other sources installable (no ghosts)', () => {
    const { lookalikes, index } = setupIndex()

    expect(lookalikes).toHaveLength(2)

    for (const entry of lookalikes) {
      expect(entry.installIdentifier).not.toBe(HUB_IDENTIFIER)
      expect(isSkillEntryInstalled(entry, index)).toBe(false)
    }
  })

  it('leaves unrelated feed rows uninstalled', () => {
    const { unrelated, index } = setupIndex()

    expect(isSkillEntryInstalled(unrelated, index)).toBe(false)
  })

  it('recognizes a feed row through its identity match alone (disjunct 2)', () => {
    const { installed } = setupIndex()
    const hubSkill: SkillInfo = {
      category: 'dev',
      description: 'defuddle workflow',
      enabled: true,
      name: 'defuddle',
      provenance: 'hub'
    }

    // Disjunct 3 says no (empty identifier set); only the matched-installed row
    // recognizes this feed row.
    const index: SkillCatalogInstallIndex = {
      skillsById: new Map([[installed.id, hubSkill]]),
      skillsByName: new Map([['defuddle', hubSkill]]),
      matchInstalled: entry => (entry.identifier === HUB_IDENTIFIER ? installed : undefined),
      officialFor: () => undefined,
      installedIdentifiers: new Set()
    }
    const feedRow = parseCatalog('skills', [
      {
        name: 'defuddle',
        description: 'same skill from the feed',
        category: 'dev',
        source: 'skills.sh',
        identifier: HUB_IDENTIFIER
      }
    ])[0]

    expect(isSkillEntryInstalled(feedRow, index)).toBe(true)
  })

  it('does not trust a matchInstalled hit whose row is not in skillsById', () => {
    const { installed } = setupIndex()

    // A match pointing at a phantom row (not among the profile's skills) must not
    // mark the entry installed — this is the `matched.id` re-check.
    const index: SkillCatalogInstallIndex = {
      skillsById: new Map(),
      skillsByName: new Map(),
      matchInstalled: () => ({ ...installed, id: 'installed:ghost' }),
      officialFor: () => undefined,
      installedIdentifiers: new Set()
    }
    const feedRow = parseCatalog('skills', [
      {
        name: 'defuddle',
        description: 'row against a phantom match',
        category: 'dev',
        source: 'skills.sh',
        identifier: 'someone/defuddle'
      }
    ])[0]

    expect(isSkillEntryInstalled(feedRow, index)).toBe(false)
  })

  it('recognizes a lock whose key differs from the frontmatter name (disjunct 3)', () => {
    const { installed } = setupIndex()
    const hubSkill: SkillInfo = {
      category: 'dev',
      description: 'defuddle workflow',
      enabled: true,
      name: 'defuddle',
      provenance: 'hub'
    }

    // Reporter's second case: the lock key is the full identifier while the
    // frontmatter name differs, so matchInstalled cannot pair them — only the
    // raw installed-identifier set recognizes the genuine row.
    const index: SkillCatalogInstallIndex = {
      skillsById: new Map([[installed.id, hubSkill]]),
      skillsByName: new Map([['defuddle', hubSkill]]),
      matchInstalled: () => undefined,
      officialFor: () => undefined,
      installedIdentifiers: new Set([HUB_IDENTIFIER])
    }
    const feedRow = parseCatalog('skills', [
      {
        name: 'defuddle',
        description: 'genuine row',
        category: 'dev',
        source: 'skills.sh',
        identifier: HUB_IDENTIFIER
      }
    ])[0]

    expect(isSkillEntryInstalled(feedRow, index)).toBe(true)
  })
})

describe('isSkillInstallBlocked', () => {
  it('blocks same-name rows whose identity differs — installing would overwrite a local skill', () => {
    const { lookalikes, index } = setupIndex()

    for (const entry of lookalikes) {
      expect(isSkillEntryInstalled(entry, index)).toBe(false)
      expect(isSkillInstallBlocked(entry, index)).toBe(true)
    }
  })

  it('leaves the true installed row and unrelated rows actionable', () => {
    const { installed, unrelated, index } = setupIndex()

    expect(isSkillInstallBlocked(installed, index)).toBe(false)
    expect(isSkillInstallBlocked(unrelated, index)).toBe(false)
  })
})
