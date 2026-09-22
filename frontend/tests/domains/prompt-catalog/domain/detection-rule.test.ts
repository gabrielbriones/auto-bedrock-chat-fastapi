import { describe, expect, it } from 'vitest'

import { parseDetectionRule } from '@/domains/prompt-catalog/domain/detection-rule'
import type { ValidationRule } from '@/domains/prompt-catalog/domain/validation-rule'

const NON_EMPTY: ValidationRule = { kind: 'nonEmpty' }

describe('parseDetectionRule', () => {
  it('never applies to select, checkbox or number (FR-PROMPT-007b)', () => {
    expect(parseDetectionRule('select', '[a-z]+', null, NON_EMPTY)).toBeNull()
    expect(parseDetectionRule('checkbox', '[a-z]+', null, NON_EMPTY)).toBeNull()
    expect(parseDetectionRule('number', '[a-z]+', null, { kind: 'numericRange', min: null, max: null })).toBeNull()
  })

  it('uses an explicit detect_pattern, defaulting flags to "i"', () => {
    expect(parseDetectionRule('text', '[0-9a-f]{8}', null, NON_EMPTY)).toEqual({
      pattern: '[0-9a-f]{8}',
      flags: 'i',
    })
  })

  it('honours an explicit detect_flags', () => {
    expect(parseDetectionRule('text', '[0-9a-f]{8}', 'g', NON_EMPTY)).toEqual({
      pattern: '[0-9a-f]{8}',
      flags: 'g',
    })
  })

  it('falls back to the validate regex with anchors stripped when no detect_pattern is set', () => {
    const rule: ValidationRule = { kind: 'regex', source: '^[0-9a-f]{8}$', invalid: false }
    expect(parseDetectionRule('text', null, null, rule)).toEqual({
      pattern: '[0-9a-f]{8}',
      flags: 'i',
    })
  })

  it('does not fall back to an invalid validate regex', () => {
    const rule: ValidationRule = { kind: 'regex', source: '([unclosed', invalid: true }
    expect(parseDetectionRule('text', null, null, rule)).toBeNull()
  })

  it('is null when there is no detect_pattern and validate is plain nonEmpty', () => {
    expect(parseDetectionRule('text', null, null, NON_EMPTY)).toBeNull()
  })
})
