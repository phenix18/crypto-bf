/**
 * The translation tables.
 *
 * A missing translation does not throw: it renders as `undefined`, or as a
 * blank element, or silently as the wrong language. None of that fails a
 * build, which is why these checks exist — they are the only thing standing
 * between adding a locale and shipping a screen with holes in it.
 */

import { describe, expect, it } from 'vitest'

import {
  LANGUAGES,
  LANGUAGE_NAMES,
  type Language,
  categoryTranslations,
  getT,
  isLanguage,
  pick,
  translations,
} from '../i18n'

/** Every leaf path in a nested object, as dotted keys. */
function paths(value: unknown, prefix = ''): string[] {
  if (value === null || typeof value !== 'object') return [prefix]
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    paths(child, prefix ? `${prefix}.${key}` : key),
  )
}

function leaves(value: unknown): string[] {
  if (value === null || typeof value !== 'object') return [String(value)]
  return Object.values(value as Record<string, unknown>).flatMap(leaves)
}

const REFERENCE: Language = 'zh'

describe('the language list', () => {
  it('has an entry in every table for each language it advertises', () => {
    for (const code of LANGUAGES) {
      expect(translations[code], `translations.${code}`).toBeTruthy()
      expect(categoryTranslations[code], `categoryTranslations.${code}`).toBeTruthy()
      expect(LANGUAGE_NAMES[code], `LANGUAGE_NAMES.${code}`).toBeTruthy()
    }
  })

  it('advertises every language that has translations', () => {
    // The switcher is built from LANGUAGES, so a locale missing from it is a
    // locale nobody can select.
    expect([...LANGUAGES].sort()).toEqual(Object.keys(translations).sort())
  })

  it('includes French', () => {
    expect(LANGUAGES).toContain('fr')
  })
})

describe('translation completeness', () => {
  const expected = paths(translations[REFERENCE]).sort()

  for (const code of LANGUAGES) {
    it(`${code} has exactly the same keys as ${REFERENCE}`, () => {
      const actual = paths(translations[code]).sort()
      expect(actual).toEqual(expected)
    })

    it(`${code} has no empty or non-string label`, () => {
      for (const [index, value] of leaves(translations[code]).entries()) {
        expect(typeof value, `${code} leaf ${expected[index]}`).toBe('string')
        expect(value.trim(), `${code} leaf ${expected[index]}`).not.toBe('')
      }
    })

    it(`${code} categories match the reference`, () => {
      expect(Object.keys(categoryTranslations[code]).sort()).toEqual(
        Object.keys(categoryTranslations[REFERENCE]).sort(),
      )
    })
  }
})

describe('the French block is actually translated', () => {
  it('is not the English one copied across', () => {
    // Some labels legitimately match - CLAW, Discussions, Positions, Note -
    // so this asks for a majority rather than for every one of them.
    const fr = leaves(translations.fr)
    const en = leaves(translations.en)
    const different = fr.filter((value, index) => value !== en[index]).length
    expect(different).toBeGreaterThan(fr.length / 2)
  })

  it('translates the categories too', () => {
    expect(categoryTranslations.fr['trading-signal']).not.toBe(
      categoryTranslations.en['trading-signal'],
    )
  })
})

describe('isLanguage', () => {
  it('accepts the languages that exist', () => {
    for (const code of LANGUAGES) expect(isLanguage(code)).toBe(true)
  })

  it('rejects anything else', () => {
    // It guards what comes back out of localStorage, which is whatever anyone
    // put there - including a locale from a build that has since shipped.
    for (const value of ['de', 'EN', '', 'français', null, undefined, 7, {}, ['fr']]) {
      expect(isLanguage(value), String(value)).toBe(false)
    }
  })
})

describe('pick', () => {
  const labels = { zh: '收盘', en: 'Close', fr: 'Fermer' }

  it('returns the label for the language asked for', () => {
    expect(pick('fr', labels)).toBe('Fermer')
    expect(pick('zh', labels)).toBe('收盘')
    expect(pick('en', labels)).toBe('Close')
  })

  it('falls back to English rather than rendering nothing', () => {
    const partial = { en: 'Close' } as Record<Language, string>
    expect(pick('fr', partial)).toBe('Close')
  })
})

describe('getT', () => {
  it('hands back the table for the language', () => {
    expect(getT('fr').nav.positions).toBe('Positions')
    expect(getT('fr').common.login).toBe('Connexion')
    expect(getT('en').common.login).toBe('Login')
  })
})
