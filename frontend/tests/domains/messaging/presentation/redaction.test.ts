import { describe, expect, it } from '@jest/globals'

import { redactSensitive, stripReasoningBlocks } from '@/domains/messaging/presentation/redaction'

describe('redactSensitive', () => {
  it('redacts credential keys but not incidental whole-word non-matches (FIX-09)', () => {
    expect(
      redactSensitive({ api_key: 'abc', secret: 'hidden', keyword: 'keep', monkey: 'keep' }),
    ).toEqual({ api_key: '[REDACTED]', secret: '[REDACTED]', keyword: 'keep', monkey: 'keep' })
  })

  it('handles cyclic objects without hanging', () => {
    const source: { secret: string; self?: unknown } = { secret: 'hidden' }
    source.self = source

    const redacted = redactSensitive(source) as { secret: string; self: unknown }

    expect(redacted.secret).toBe('[REDACTED]')
    expect(redacted.self).toBe(redacted)
  })
})

describe('stripReasoningBlocks', () => {
  it('removes model-only reasoning blocks before rendering', () => {
    expect(stripReasoningBlocks('<thinking>private chain</thinking>Visible answer')).toBe('Visible answer')
  })
})