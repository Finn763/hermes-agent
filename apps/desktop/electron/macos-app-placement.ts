// macOS first-launch placement helpers (#42501; root-caused in #41518).
// Pure logic extracted so vitest can cover it without Electron: main.ts keeps
// only the thin app/exec wrappers. Restores what commit 9d07927a2 removed
// (originally 746618217, tile format fixed by 1daecfa4b).

export interface RelocateState {
  isMac: boolean
  isPackaged: boolean
  noAutoMove: boolean
  isInApplicationsFolder: boolean
}

export interface DockPinState {
  isMac: boolean
  isPackaged: boolean
  noDockPin: boolean
  markerExists: boolean
  isInApplicationsFolder: boolean
  bundle: string | null
}

// The Dock stores tiles as file-reference URLs (type 15), e.g.
// file:///Applications/Hermes.app/ -- NOT a raw POSIX path. A type-0/raw-path
// tile is silently dropped when the Dock rewrites persistent-apps on restart.
export function dockTileUrl(bundle: string): string {
  const withSlash = bundle.endsWith('/') ? bundle : `${bundle}/`
  return `file://${withSlash}`
}

export function buildDockTile(url: string): string {
  return (
    '<dict><key>tile-data</key><dict><key>file-data</key><dict>' +
    `<key>_CFURLString</key><string>${url}</string><key>_CFURLStringType</key><integer>15</integer>` +
    '</dict></dict></dict>'
  )
}

export function shouldAttemptRelocate(state: RelocateState): boolean {
  return state.isMac && state.isPackaged && !state.noAutoMove && !state.isInApplicationsFolder
}

export function shouldPinDock(state: DockPinState): boolean {
  return (
    state.isMac &&
    state.isPackaged &&
    !state.noDockPin &&
    !state.markerExists &&
    state.isInApplicationsFolder &&
    !!state.bundle
  )
}

export function dockTileAlreadyPresent(defaultsOutput: string | null, url: string): boolean {
  return !!defaultsOutput && defaultsOutput.includes(url)
}
