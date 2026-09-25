import { describe, expect, it } from '@jest/globals'

import { normalizeInputType, PROMPT_INPUT_TYPES } from '@/domains/prompt-catalog/domain/input-type'

describe('normalizeInputType', () => {
  it.each(PROMPT_INPUT_TYPES)('passes a known type "%s" through unchanged', (type) => {
    expect(normalizeInputType(type)).toBe(type)
  })

  it('degrades an unrecognised input_type to text (FR-PROMPT-002)', () => {
    expect(normalizeInputType('date')).toBe('text')
  })

  it('degrades a missing input_type to text', () => {
    expect(normalizeInputType(null)).toBe('text')
    expect(normalizeInputType(undefined)).toBe('text')
  })
})
