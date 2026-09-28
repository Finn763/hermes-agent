import { describe, expect, it } from 'vitest'

import { detectBundleSkew, isFallbackCommit, type RunGit } from './bundle-skew'

const REPO = '/repo'
const STAMP = { commit: 'a'.repeat(40), source: 'ci' }

function gitReturning(stdout: string, code = 0): RunGit {
  return async () => ({ code, stderr: '', stdout })
}

describe('isFallbackCommit', () => {
  it('matches the all-zero placeholder at any stamp length', () => {
    expect(isFallbackCommit('0'.repeat(40))).toBe(true)
    expect(isFallbackCommit('0'.repeat(7))).toBe(true)
    expect(isFallbackCommit('a'.repeat(40))).toBe(false)
  })
})

describe('detectBundleSkew', () => {
  it('reports stale when desktop commits landed after the stamp', async () => {
    const result = await detectBundleSkew(STAMP, gitReturning('3\n'), REPO)

    expect(result).toEqual({ desktopCommitsBehind: 3, outOfSync: true })
  })

  it('passes the stamp range scoped to apps/desktop', async () => {
    let seen: string[] = []

    const git: RunGit = async args => {
      seen = args

      return { code: 0, stderr: '', stdout: '0' }
    }

    await detectBundleSkew(STAMP, git, REPO)

    expect(seen).toEqual(['rev-list', '--count', `${STAMP.commit}..HEAD`, '--', 'apps/desktop'])
  })

  it('is quiet when no desktop commits follow the stamp', async () => {
    const result = await detectBundleSkew(STAMP, gitReturning('0\n'), REPO)

    expect(result).toEqual({ desktopCommitsBehind: 0, outOfSync: false })
  })

  it('is quiet without a stamp (dev runs)', async () => {
    expect(await detectBundleSkew(null, gitReturning('9'), REPO)).toEqual({
      desktopCommitsBehind: null,
      outOfSync: false
    })
  })

  it('is quiet on a fallback stamp (non-git build)', async () => {
    const fallback = { commit: '0'.repeat(40), source: 'fallback' }

    expect(await detectBundleSkew(fallback, gitReturning('9'), REPO)).toEqual({
      desktopCommitsBehind: null,
      outOfSync: false
    })
  })

  it('is quiet when git fails (unknown commit, shallow clone, no git)', async () => {
    expect(await detectBundleSkew(STAMP, gitReturning('', 128), REPO)).toEqual({
      desktopCommitsBehind: null,
      outOfSync: false
    })
  })

  it('is quiet when git throws', async () => {
    const git: RunGit = async () => {
      throw new Error('spawn ENOENT')
    }

    expect(await detectBundleSkew(STAMP, git, REPO)).toEqual({
      desktopCommitsBehind: null,
      outOfSync: false
    })
  })

  it('is quiet on unparsable rev-list output', async () => {
    expect(await detectBundleSkew(STAMP, gitReturning('fatal: bad object'), REPO)).toEqual({
      desktopCommitsBehind: null,
      outOfSync: false
    })
  })

  it(
    'gives up instead of hanging when git never resolves (tree:0 promisor stall)',
    { timeout: 50_000 },
    async () => {
      const seenOptions: unknown[] = []
      const hung: RunGit = (_args, options) => {
        seenOptions.push(options)
        return new Promise<{ code: number; stderr: string; stdout: string }>(() => {})
      }

      const result = await detectBundleSkew(STAMP, hung, REPO, { timeoutMs: 200 })

      expect(result).toEqual({ desktopCommitsBehind: null, outOfSync: false })
      expect(seenOptions).toEqual([{ cwd: REPO, timeoutMs: 200 }])
    }
  )
})
