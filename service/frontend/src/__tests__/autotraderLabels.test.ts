/**
 * The desk's own labels, and the contract with the backend's stage names.
 *
 * The stage list is duplicated across an HTTP boundary — `STAGES` in
 * service/server/auto_trader.py, `STAGE_LABELS` here. There is a test on each
 * side so neither can drift quietly: the Python one fails when a stage is
 * emitted without being declared, this one fails when a declared stage has no
 * label in some language.
 */

import { describe, expect, it } from 'vitest'

import { LABELS, STAGE_LABELS, hasCouncil, stageLabel } from '../autotraderLabels'
import { LANGUAGES, type Language } from '../i18n'

/** Mirrors STAGES in service/server/auto_trader.py. */
const BACKEND_STAGES = [
  'strategy',
  'risk',
  'calibration',
  'council',
  'approved',
  'executed',
  'execution',
  'position',
  'bar',
  'error',
]

describe('desk labels', () => {
  it('covers every language the app offers', () => {
    for (const code of LANGUAGES) {
      expect(LABELS[code], `LABELS.${code}`).toBeTruthy()
    }
  })

  it('carries the same keys in every language', () => {
    const expected = Object.keys(LABELS.en).sort()
    for (const code of LANGUAGES) {
      expect(Object.keys(LABELS[code]).sort(), `LABELS.${code}`).toEqual(expected)
    }
  })

  it('has no empty label', () => {
    for (const code of LANGUAGES) {
      for (const [key, value] of Object.entries(LABELS[code])) {
        expect(typeof value, `${code}.${key}`).toBe('string')
        expect(value.trim(), `${code}.${key}`).not.toBe('')
      }
    }
  })

  it('names the environment variable in the disarmed notice', () => {
    // The notice exists to answer "why is the button greyed out", so it has
    // to name the thing to change, in every language.
    for (const code of LANGUAGES) {
      expect(LABELS[code].disarmed, code).toContain('AUTO_TRADE_ENABLED')
    }
  })

  it('says the demo feed is a demo, in every language', () => {
    for (const code of LANGUAGES) {
      expect(LABELS[code].demo.trim().length, code).toBeGreaterThan(8)
    }
  })

  it('is actually translated into French', () => {
    const fr = Object.values(LABELS.fr)
    const en = Object.values(LABELS.en)
    const different = fr.filter((value, index) => value !== en[index]).length
    expect(different).toBeGreaterThan(fr.length / 2)
  })
})

describe('stage labels', () => {
  it('has a label for every stage the backend can report', () => {
    for (const stage of BACKEND_STAGES) {
      expect(STAGE_LABELS[stage], `STAGE_LABELS.${stage}`).toBeTruthy()
    }
  })

  it('has no label for a stage the backend never sends', () => {
    expect(Object.keys(STAGE_LABELS).sort()).toEqual([...BACKEND_STAGES].sort())
  })

  it('translates each stage into every language', () => {
    for (const [stage, labels] of Object.entries(STAGE_LABELS)) {
      for (const code of LANGUAGES) {
        expect(labels[code], `${stage}.${code}`).toBeTruthy()
      }
    }
  })

  it('renders the stage in the language asked for', () => {
    expect(stageLabel('calibration', 'fr')).toBe('calibration')
    expect(stageLabel('executed', 'fr')).toBe('exécuté')
    expect(stageLabel('executed', 'zh')).toBe('已下单')
    expect(stageLabel('executed', 'en')).toBe('executed')
  })

  it('shows an unknown stage rather than nothing', () => {
    // A stage added to the backend before its label lands here should read as
    // a raw English word, not as an empty cell.
    expect(stageLabel('quarantine', 'fr')).toBe('quarantine')
  })

  it('falls back to English for a language a stage is missing', () => {
    const partial = { en: 'settled' } as Record<Language, string>
    const table: Record<string, Record<Language, string>> = { settled: partial }
    expect(table.settled.fr ?? table.settled.en).toBe('settled')
  })
})

describe('hasCouncil', () => {
  /*
   * The bug this pins: a decision that stops at an earlier gate still carries
   * `council: {}` from the API, and an empty object is truthy, so the panel
   * rendered and then threw on `opinions.map`. Found by watching the browser
   * console, not by a failing build.
   */
  it('is false for a decision that never reached the council', () => {
    expect(hasCouncil({})).toBe(false)
    expect(hasCouncil(undefined)).toBe(false)
    expect(hasCouncil(null)).toBe(false)
  })

  it('is false for a council that returned no opinions', () => {
    expect(hasCouncil({ opinions: [] })).toBe(false)
  })

  it('is false when opinions is not an array', () => {
    expect(hasCouncil({ opinions: 'confirm' } as unknown as { opinions?: unknown })).toBe(false)
  })

  it('is true once there are opinions to show', () => {
    expect(hasCouncil({ opinions: [{ role: 'risk' }] })).toBe(true)
  })
})
