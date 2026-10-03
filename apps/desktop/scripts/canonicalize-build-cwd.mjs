// When the checkout lives under a Windows directory junction (e.g. a drive
// mapped onto the user profile, #44902), `vite build` resolves the HTML entry
// to its real target-drive path while the build root stays on the junction
// path, and rolldown rejects the emitted absolute fileName. Canonicalizing the
// cwd first keeps root and entry on one path. Imported for its side effect by
// vite.config.ts so every build entrypoint (installer, CLI, manual) is covered.
import fs from 'node:fs'

export function canonicalizeBuildCwd(cwd = process.cwd()) {
  try {
    const real = fs.realpathSync(cwd)
    if (real !== cwd) process.chdir(real)
  } catch {
    // Best effort: an unresolvable cwd still builds the old way.
  }
  return process.cwd()
}

canonicalizeBuildCwd()
