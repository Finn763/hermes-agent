import { describe, expect, it, vi } from 'vitest'

vi.mock('@/hermes', () => ({
  getHermesConfigRecord: vi.fn(async () => ({})),
  saveHermesConfig: vi.fn(async () => undefined)
}))

import { saveHermesConfig } from '@/hermes'

import {
  $voiceLiveIdleHangupSeconds,
  $voiceStopPhrase,
  applyVoiceLiveIdleHangupFromConfig,
  applyVoiceStopPhraseFromConfig
} from './voice-prefs'

it('keeps the desktop toggle local across config refreshes', async () => {
  for (const fails of [false, true]) {
    for (const enabled of [false, true]) {
      localStorage.clear()
      vi.resetModules()
      const prefs = await import('./voice-prefs')
      const write = vi.spyOn(localStorage, 'setItem')

      if (fails) {
        write.mockImplementation(() => {
          throw new DOMException('Full', 'QuotaExceededError')
        })
      }

      vi.mocked(saveHermesConfig).mockClear()

      try {
        await prefs.setAutoSpeakReplies(enabled)
        prefs.applyAutoSpeakFromConfig({ voice: { auto_tts: !enabled } })
        expect(prefs.$autoSpeakReplies.get()).toBe(enabled)
        expect(saveHermesConfig).not.toHaveBeenCalled()
        expect(localStorage.getItem('hermes.desktop.autoSpeakReplies')).toBe(fails ? null : String(enabled))
      } finally {
        write.mockRestore()
      }
    }
  }
})

it('migrates the legacy preference once, not on every refresh', async () => {
  for (const fails of [false, true]) {
    for (const enabled of [false, true]) {
      localStorage.clear()
      vi.resetModules()
      const prefs = await import('./voice-prefs')
      const write = vi.spyOn(localStorage, 'setItem')

      if (fails) {
        write.mockImplementation(() => {
          throw new DOMException('Denied', 'SecurityError')
        })
      }

      try {
        prefs.applyAutoSpeakFromConfig(null)
        expect(localStorage.getItem('hermes.desktop.autoSpeakReplies')).toBeNull()
        prefs.applyAutoSpeakFromConfig({ voice: { auto_tts: enabled } })
        prefs.applyAutoSpeakFromConfig({ voice: { auto_tts: !enabled } })
        expect(prefs.$autoSpeakReplies.get()).toBe(enabled)
        expect(localStorage.getItem('hermes.desktop.autoSpeakReplies')).toBe(fails ? null : String(enabled))
      } finally {
        write.mockRestore()
      }
    }
  }
})

describe('applyVoiceStopPhraseFromConfig', () => {
  it('defaults to "stop" when the key is absent (backend default applies)', () => {
    applyVoiceStopPhraseFromConfig({ voice: {} })
    expect($voiceStopPhrase.get()).toBe('stop')

    applyVoiceStopPhraseFromConfig(null)
    expect($voiceStopPhrase.get()).toBe('stop')
  })

  it('uses the first configured phrase so a custom phrase renders correctly', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: ['goodbye hermes', 'stop'] } })
    expect($voiceStopPhrase.get()).toBe('goodbye hermes')
  })

  it('coerces a bare string like the backend does', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: 'halt' } })
    expect($voiceStopPhrase.get()).toBe('halt')
  })

  it('null phrase when stop phrases are disabled — no notice is shown', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: [] } })
    expect($voiceStopPhrase.get()).toBeNull()
  })

  it('malformed entries are skipped; all-blank list disables', () => {
    applyVoiceStopPhraseFromConfig({ voice: { stop_phrases: ['  ', ''] } })
    expect($voiceStopPhrase.get()).toBeNull()
  })
})

describe('applyVoiceLiveIdleHangupFromConfig', () => {
  it('defaults to 300s when the key is absent or unreadable', () => {
    for (const config of [
      { voice: {} },
      { voice: { gpt_live: {} } },
      null,
      { voice: { gpt_live: { idle_hangup_seconds: 'lot' } } }
    ]) {
      $voiceLiveIdleHangupSeconds.set(1)
      applyVoiceLiveIdleHangupFromConfig(config)
      expect($voiceLiveIdleHangupSeconds.get()).toBe(300)
    }
  })

  it('takes the configured deadline', () => {
    applyVoiceLiveIdleHangupFromConfig({ voice: { gpt_live: { idle_hangup_seconds: 45 } } })
    expect($voiceLiveIdleHangupSeconds.get()).toBe(45)
  })

  it('keeps 0 — the deadline is off, not a fall back to the default', () => {
    applyVoiceLiveIdleHangupFromConfig({ voice: { gpt_live: { idle_hangup_seconds: 0 } } })
    expect($voiceLiveIdleHangupSeconds.get()).toBe(0)
  })

  it('refuses a negative deadline', () => {
    applyVoiceLiveIdleHangupFromConfig({ voice: { gpt_live: { idle_hangup_seconds: -5 } } })
    expect($voiceLiveIdleHangupSeconds.get()).toBe(300)
  })
})
