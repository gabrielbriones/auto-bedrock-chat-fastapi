import { describe, expect, it } from 'vitest'

import { PROMPT_CATALOG_COPY } from '@/shared/copy/prompt-catalog'
import { variableFieldError } from '@/domains/prompt-catalog/presentation/variable-error'

describe('variableFieldError', () => {
  it('is null when the value validates', () => {
    expect(variableFieldError({ kind: 'nonEmpty' }, { kind: 'text', value: 'abc' })).toBeNull()
  })

  it('reports the shared "required" message for nonEmpty/regex failures', () => {
    expect(variableFieldError({ kind: 'nonEmpty' }, { kind: 'text', value: '' })).toBe(
      PROMPT_CATALOG_COPY.errors.required,
    )
    expect(
      variableFieldError({ kind: 'regex', source: '^[0-9]+$', invalid: false }, { kind: 'text', value: 'abc' }),
    ).toBe(PROMPT_CATALOG_COPY.errors.required)
  })

  it('reports a range message when both min and max are set', () => {
    expect(
      variableFieldError({ kind: 'numericRange', min: 1, max: 10 }, { kind: 'number', value: 0 }),
    ).toBe(PROMPT_CATALOG_COPY.errors.numberRange(1, 10))
  })

  it('reports a min-only message', () => {
    expect(
      variableFieldError({ kind: 'numericRange', min: 1, max: null }, { kind: 'number', value: 0 }),
    ).toBe(PROMPT_CATALOG_COPY.errors.numberMin(1))
  })

  it('reports a max-only message', () => {
    expect(
      variableFieldError({ kind: 'numericRange', min: null, max: 10 }, { kind: 'number', value: 11 }),
    ).toBe(PROMPT_CATALOG_COPY.errors.numberMax(10))
  })

  it('reports a generic number message when neither bound is set but the value is non-finite', () => {
    expect(
      variableFieldError({ kind: 'numericRange', min: null, max: null }, { kind: 'number', value: Number.NaN }),
    ).toBe(PROMPT_CATALOG_COPY.errors.numberInvalid)
  })
})
