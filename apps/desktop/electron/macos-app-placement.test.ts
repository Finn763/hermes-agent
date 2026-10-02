import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  buildDockTile,
  dockTileAlreadyPresent,
  dockTileUrl,
  shouldAttemptRelocate,
  shouldPinDock,
} from './macos-app-placement'

const BUNDLE = '/Applications/Hermes.app'
const URL = 'file:///Applications/Hermes.app/'

test('dock tile URL is a file-reference URL with trailing slash', () => {
  assert.equal(dockTileUrl(BUNDLE), URL)
  assert.equal(dockTileUrl(`${BUNDLE}/`), URL)
})

test('dock tile is a type-15 file-reference tile, not a raw path', () => {
  const tile = buildDockTile(URL)
  assert.match(tile, /_CFURLString<\/key><string>file:\/\/\/Applications\/Hermes\.app\/<\/string>/)
  assert.match(tile, /_CFURLStringType<\/key><integer>15<\/integer>/)
  assert.doesNotMatch(tile, /<integer>0<\/integer>/)
})

test('relocation is attempted only for packaged macOS builds outside /Applications', () => {
  const base = { isMac: true, isPackaged: true, noAutoMove: false, isInApplicationsFolder: false }
  assert.equal(shouldAttemptRelocate(base), true)
  assert.equal(shouldAttemptRelocate({ ...base, isInApplicationsFolder: true }), false)
  assert.equal(shouldAttemptRelocate({ ...base, isMac: false }), false)
  assert.equal(shouldAttemptRelocate({ ...base, isPackaged: false }), false)
  assert.equal(shouldAttemptRelocate({ ...base, noAutoMove: true }), false)
})

test('dock pin runs once, only for the canonical /Applications copy', () => {
  const base = {
    isMac: true,
    isPackaged: true,
    noDockPin: false,
    markerExists: false,
    isInApplicationsFolder: true,
    bundle: BUNDLE,
  }
  assert.equal(shouldPinDock(base), true)
  assert.equal(shouldPinDock({ ...base, markerExists: true }), false)
  assert.equal(shouldPinDock({ ...base, isInApplicationsFolder: false }), false)
  assert.equal(shouldPinDock({ ...base, bundle: null }), false)
  assert.equal(shouldPinDock({ ...base, isMac: false }), false)
  assert.equal(shouldPinDock({ ...base, noDockPin: true }), false)
})

test('existing dock tile suppresses the duplicate pin', () => {
  assert.equal(dockTileAlreadyPresent(`( ${URL} )`, URL), true)
  assert.equal(dockTileAlreadyPresent('( file:///Other.app/ )', URL), false)
  assert.equal(dockTileAlreadyPresent(null, URL), false)
})
