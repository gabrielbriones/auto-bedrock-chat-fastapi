import { describe, expect, it } from 'vitest'

import {
  composerAvailability,
  composerKeyIntent,
  type ComposerConditions,
  type ComposerKeyIntent,
  type ComposerKeystroke,
} from '@/domains/messaging/presentation/composer-policy'

const REASONS = {
  unauthenticated: 'auth',
  offline: 'offline',
  responding: 'responding',
} as const

const conditions = (overrides: Partial<ComposerConditions> = {}): ComposerConditions => ({
  inputEnabled: true,
  connected: true,
  awaitingResponse: false,
  lockWhileResponding: true,
  ...overrides,
})

describe('composerAvailability', () => {
  // FR-MSG-012/012a/024: every combination, so a new condition cannot be added without a decision
  // about how it interacts with the others.
  const cases: readonly (readonly [string, Partial<ComposerConditions>, boolean, string | null])[] =
    [
      ['open when authenticated, connected and idle', {}, true, null],
      ['closed while unauthenticated', { inputEnabled: false }, false, REASONS.unauthenticated],
      ['closed while the socket is down', { connected: false }, false, REASONS.offline],
      [
        'closed mid-turn when locking is configured',
        { awaitingResponse: true },
        false,
        REASONS.responding,
      ],
      [
        'open mid-turn when locking is not configured',
        { awaitingResponse: true, lockWhileResponding: false },
        true,
        null,
      ],
      [
        'reports auth ahead of the connection, because reconnecting would not help',
        { inputEnabled: false, connected: false },
        false,
        REASONS.unauthenticated,
      ],
      [
        'reports the connection ahead of the in-flight turn',
        { connected: false, awaitingResponse: true },
        false,
        REASONS.offline,
      ],
    ]

  it.each(cases)('%s', (_name, overrides, enabled, reason) => {
    expect(composerAvailability(conditions(overrides), REASONS)).toEqual({ enabled, reason })
  })

  it('states a reason exactly when it is closed', () => {
    for (const [, overrides] of cases) {
      const availability = composerAvailability(conditions(overrides), REASONS)

      expect(availability.enabled).toBe(availability.reason === null)
    }
  })

  // A turn recycled by staleness or a drop resolves, so `awaitingResponse` falls and the composer
  // reopens without any separate unlock path (FR-MSG-005).
  it('reopens when a locked turn recycles', () => {
    const locked = composerAvailability(conditions({ awaitingResponse: true }), REASONS)
    const recycled = composerAvailability(conditions({ awaitingResponse: false }), REASONS)

    expect(locked.enabled).toBe(false)
    expect(recycled.enabled).toBe(true)
  })
})

const keystroke = (overrides: Partial<ComposerKeystroke> = {}): ComposerKeystroke => ({
  key: 'Enter',
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  isComposing: false,
  ...overrides,
})

describe('composerKeyIntent', () => {
  // FR-MSG-010. Every row of the matrix, including the modifiers a browser would not turn into a
  // newline on its own.
  const matrix: readonly (readonly [
    string,
    Partial<ComposerKeystroke>,
    { readonly locked: boolean; readonly hasText: boolean },
    ComposerKeyIntent,
  ])[] = [
    ['Enter with a draft sends', {}, { locked: false, hasText: true }, 'send'],
    ['Enter with no draft does nothing', {}, { locked: false, hasText: false }, 'ignore'],
    ['Enter while locked does nothing', {}, { locked: true, hasText: true }, 'ignore'],
    ['Shift+Enter is a newline', { shiftKey: true }, { locked: false, hasText: true }, 'newline'],
    ['Ctrl+Enter is a newline', { ctrlKey: true }, { locked: false, hasText: true }, 'newline'],
    ['Meta+Enter is a newline', { metaKey: true }, { locked: false, hasText: true }, 'newline'],
    ['Alt+Enter is a newline', { altKey: true }, { locked: false, hasText: true }, 'newline'],
    [
      'Shift+Enter with no draft is still a newline',
      { shiftKey: true },
      { locked: false, hasText: false },
      'newline',
    ],
    [
      'the Enter that commits an IME candidate never sends',
      { isComposing: true },
      { locked: false, hasText: true },
      'ignore',
    ],
    [
      'a composing Shift+Enter is also swallowed',
      { isComposing: true, shiftKey: true },
      { locked: false, hasText: true },
      'ignore',
    ],
    ['Escape is left to the ancestors', { key: 'Escape' }, { locked: false, hasText: true }, 'pass'],
    ['Tab is left alone', { key: 'Tab' }, { locked: false, hasText: true }, 'pass'],
    ['a printable key is left alone', { key: 'a' }, { locked: false, hasText: true }, 'pass'],
  ]

  it.each(matrix)('%s', (_name, stroke, context, intent) => {
    expect(composerKeyIntent(keystroke(stroke), context)).toBe(intent)
  })

  it('never sends while locked, whatever the modifiers', () => {
    for (const modifier of ['shiftKey', 'ctrlKey', 'metaKey', 'altKey', null] as const) {
      const stroke = keystroke(modifier === null ? {} : { [modifier]: true })

      expect(composerKeyIntent(stroke, { locked: true, hasText: true })).not.toBe('send')
    }
  })
})
