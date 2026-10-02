/**
 * backend-probes.ts
 *
 * Cheap "does this candidate backend actually work" checks used by
 * resolveHermesBackend (main.ts). The resolver walks a ladder of
 * candidates -- bootstrap marker, `hermes` on PATH, system Python with
 * hermes_cli installed -- and historically returned the first candidate
 * whose binary existed on disk. That assumption breaks when a user has
 * a pre-installed Python 3.11-3.13 (so findSystemPython() returns a
 * path) but no hermes_cli in its site-packages: the resolver hands back
 * a backend the spawn step can't actually run, and the user gets a
 * dead-on-arrival "ModuleNotFoundError: No module named 'hermes_cli'"
 * instead of the first-launch installer.
 *
 * These probes give the resolver a way to verify a candidate before
 * trusting it. Failure (non-zero exit, exception, timeout) means "skip
 * this rung, try the next one"; success means "spawn this for real."
 * Falling off the bottom of the ladder lands on the bootstrap-needed
 * sentinel, which is exactly what we want when nothing pre-existing
 * actually works.
 *
 * Both probes are deliberately fast and forgiving:
 *   - default 15s timeout (5s was too short on cold Windows disks / AV;
 *     issue #61764 death-loop) with HERMES_PROBE_TIMEOUT_MS override
 *   - one automatic retry after a timeout before declaring the runtime dead
 *   - stdio ignored (we only care about exit code; stdout/stderr are
 *     not surfaced to the user, just to recentHermesLog for forensics
 *     via the caller's catch block if it chooses)
 *   - any throw -> false (never propagate -- resolver wants a boolean)
 *
 * Kept in a standalone ts module so it can be unit-tested with
 * `node --test` without dragging in the electron runtime (same pattern
 * as bootstrap-platform.ts and hardening.ts).
 */

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

/** Default probe budget. 5s false-negativeed healthy Windows cold starts (#61764). */
const DEFAULT_PROBE_TIMEOUT_MS = 15_000

/**
 * Resolve the backend probe timeout (ms).
 * Honours HERMES_PROBE_TIMEOUT_MS when it parses as a positive integer.
 */
function resolveProbeTimeoutMs(env: NodeJS.ProcessEnv = process.env): number {
  const raw = env.HERMES_PROBE_TIMEOUT_MS

  if (raw == null || raw === '') {
    return DEFAULT_PROBE_TIMEOUT_MS
  }

  const n = Number.parseInt(String(raw), 10)

  if (!Number.isFinite(n) || n <= 0) {
    return DEFAULT_PROBE_TIMEOUT_MS
  }

  // Clamp absurd values (ms) so a typo can't hang startup forever.
  return Math.min(n, 120_000)
}

const PROBE_TIMEOUT_MS = resolveProbeTimeoutMs()

function isTimeoutError(err: unknown): boolean {
  if (!err || typeof err !== 'object') {
    return false
  }

  const e = err as { code?: string; killed?: boolean; signal?: string }

  if (e.killed === true) {
    return true
  }

  if (e.code === 'ETIMEDOUT') {
    return true
  }

  // Node marks timed-out execFileSync with SIGTERM on some platforms.
  if (e.signal === 'SIGTERM') {
    return true
  }

  return false
}

/**
 * Run execFileSync; on timeout only, retry once before failing.
 * Non-timeout failures (ENOENT, non-zero exit) fail immediately.
 */
function execProbeSync(
  command: string,
  args: string[],
  options: {
    cwd?: string
    env?: NodeJS.ProcessEnv
    stdio: 'ignore'
    timeout: number
    shell?: boolean
    windowsHide?: boolean
  }
): void {
  try {
    execFileSync(command, args, options)
  } catch (err) {
    if (!isTimeoutError(err)) {
      throw err
    }

    // One cold-cache / AV miss should not force hermes-setup --update (#61764).
    execFileSync(command, args, options)
  }
}

/**
 * Return the Python snippet used to verify Hermes can import far enough to
 * launch the CLI. Kept exported for tests so dependency regressions are
 * caught without needing a real broken venv fixture.
 *
 * @returns {string}
 */
function hermesRuntimeImportProbe() {
  // dashboard_auth is the backend's first heavy import on the serve path
  // (via web_server); --version exits through the ultrafast fast path
  // before any of this loads, so a global install missing dashboard_auth
  // answers --version fine and then crash-loops the backend (#40702).
  return 'import yaml; import dotenv; import hermes_cli.config; import hermes_cli.dashboard_auth'
}

/**
 * Return true iff the Hermes runtime import probe exits 0.
 *
 * Used to gate the "fallback to system Python with hermes_cli installed"
 * rung of resolveHermesBackend. Without this, a system Python 3.11-3.13
 * registered in PEP 514 makes findSystemPython() succeed regardless of
 * whether hermes_cli has actually been pip-installed into its
 * site-packages -- and the resolver returns a backend that immediately
 * dies on spawn.
 *
 * The probe intentionally imports through hermes_cli.dashboard_auth, not
 * just the top-level package: a broken/empty Windows launcher venv can
 * still see the source tree through PYTHONPATH but lack PyYAML, then die
 * on the first real CLI import -- and a system-wide install missing
 * dashboard_auth answers --version fine but crash-loops the backend (#40702).
 *
 * @param {string} pythonPath - Absolute path to a python.exe / python.
 * @param {object} [opts.env] - Additional environment for the probe.
 * @returns {boolean}
 */
function canImportHermesCli(pythonPath: string, opts: { env?: Record<string, string> } = {}) {
  if (!pythonPath) {
    return false
  }

  try {
    execProbeSync(pythonPath, ['-c', hermesRuntimeImportProbe()], {
      env: { ...process.env, ...(opts.env || {}) },
      stdio: 'ignore',
      timeout: PROBE_TIMEOUT_MS,
      windowsHide: true
    })

    return true
  } catch {
    return false
  }
}

/**
 * Return true iff `<hermesCommand> --version` exits 0.
 *
 * Used to gate the "existing `hermes` on PATH" rung. Without this, a
 * stale hermes.cmd shim left behind by an uninstalled pip install (or
 * a half-built venv whose `hermes` entry-point points at a deleted
 * Python) survives findOnPath() and gets selected as the backend.
 *
 * We intentionally avoid invoking the command with the dashboard args
 * here -- `--version` is the cheapest "is this binary alive" smoke
 * test that every hermes_cli entry-point has supported since 0.1.
 *
 * @param {string} hermesCommand - Resolved absolute path to a hermes
 *   executable (or an interpreter+script wrapper).
 * @param {boolean} [opts.shell] - Whether to run through a shell. For
 *   .cmd/.bat shims on Windows execFileSync needs shell:true to find
 *   the cmd interpreter; mirrors the same flag isCommandScript() drives
 *   in resolveHermesBackend.
 * @returns {boolean}
 */
/**
 * An explicit desktop backend command is a deployment contract, not a PATH
 * discovery candidate. In particular, the Nix desktop wrapper points this at
 * its immutable, matching Hermes package; it must never fall through to the
 * mutable install-script bootstrap path if a best-effort probe is slow.
 */
function shouldTrustHermesOverride(hermesOverride?: string) {
  return typeof hermesOverride === 'string' && hermesOverride.trim().length > 0
}

/**
 * Find the interpreter behind a Windows `hermes` console-script shim so the
 * resolver can probe it directly instead of trusting `--version`.
 *
 * Both pip layouts are covered: `<prefix>\Scripts\python.exe` (venv) and
 * `<prefix>\python.exe` (global CPython install, the #40702 repro shape
 * `C:\Python313\Scripts\hermes.EXE`). Returns null when the command is not
 * a Scripts\hermes(.exe) shim or no sibling interpreter exists on disk.
 *
 * Uses path.win32 so behaviour is identical on every CI platform.
 */
// ponytail: only Scripts\hermes(.exe) layouts get the import probe; other
// wrappers (.cmd shims, nix store paths) keep the legacy --version check.
function siblingPythonForHermesCommand(
  hermesCommand: string,
  opts: { exists?: (p: string) => boolean } = {}
): string | null {
  if (!hermesCommand || typeof hermesCommand !== 'string') {
    return null
  }

  const win = path.win32
  const command = String(hermesCommand)

  if (!/^hermes(?:\.exe)?$/i.test(win.basename(command))) {
    return null
  }

  const scriptsDir = win.dirname(command)

  if (win.basename(scriptsDir).toLowerCase() !== 'scripts') {
    return null
  }

  const exists =
    opts.exists ||
    ((p: string) => {
      try {
        return fs.statSync(p).isFile()
      } catch {
        return false
      }
    })

  const prefix = win.dirname(scriptsDir)
  const venvPython = win.join(prefix, 'Scripts', 'python.exe')

  if (exists(venvPython)) {
    return venvPython
  }

  const globalPython = win.join(prefix, 'python.exe')

  if (exists(globalPython)) {
    return globalPython
  }

  return null
}

function verifyHermesCli(hermesCommand: string, opts?: { shell?: boolean }) {
  if (!hermesCommand) {
    return false
  }

  // A Windows pip shim answers --version through the ultrafast fast path
  // before heavy imports load, so --version cannot see a broken install
  // missing dashboard_auth (#40702). When the interpreter behind the shim
  // is on disk, run the import probe on it instead: failure falls through
  // to bootstrap, which also un-breaks "Repair/Reinstall" (it re-resolved
  // the same dead global binary every cycle).
  const sibling = siblingPythonForHermesCommand(hermesCommand)

  if (sibling) {
    return canImportHermesCli(sibling)
  }

  try {
    execProbeSync(hermesCommand, ['--version'], {
      stdio: 'ignore',
      timeout: PROBE_TIMEOUT_MS,
      shell: Boolean(opts?.shell),
      windowsHide: true
    })

    return true
  } catch {
    return false
  }
}

export {
  canImportHermesCli,
  DEFAULT_PROBE_TIMEOUT_MS,
  execProbeSync,
  hermesRuntimeImportProbe,
  PROBE_TIMEOUT_MS,
  resolveProbeTimeoutMs,
  shouldTrustHermesOverride,
  siblingPythonForHermesCommand,
  verifyHermesCli
}
