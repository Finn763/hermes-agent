import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test } from 'vitest'

// #44902: when the checkout lives under a Windows directory junction,
// `vite build` resolves the HTML entry to its real (target-drive) path while
// the build root stays on the junction path, and rolldown rejects the emitted
// absolute fileName. The shipped helper must canonicalize the process cwd to
// its real path on import, so every `vite build` (installer, CLI, manual)
// builds from one consistent root.
const helper = new URL('./canonicalize-build-cwd.mjs', import.meta.url)

test('importing the build-cwd helper from under a junction lands on the real path', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-junction-'))
  const target = path.join(root, 'real')
  fs.mkdirSync(target)
  const link = path.join(root, 'link')
  try {
    fs.symlinkSync(target, link, process.platform === 'win32' ? 'junction' : 'dir')
  } catch (error) {
    if (error.code === 'EPERM' || error.code === 'EACCES') return // skip: junctions not permitted here
    throw error
  }
  try {
    const out = execFileSync(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        `await import(${JSON.stringify(helper.href)}); process.stdout.write(process.cwd())`
      ],
      { cwd: link, encoding: 'utf8' }
    )
    assert.equal(out, fs.realpathSync(link))
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
