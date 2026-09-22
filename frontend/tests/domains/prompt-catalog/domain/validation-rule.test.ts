import { describe, expect, it } from 'vitest'

import { parseValidationRule, validateValue } from '@/domains/prompt-catalog/domain/validation-rule'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'

const text = (value: string): VariableValue => ({ kind: 'text', value })
const select = (value: string): VariableValue => ({ kind: 'select', value })
const number = (value: number): VariableValue => ({ kind: 'number', value })
const boolean = (value: boolean): VariableValue => ({ kind: 'boolean', value })

describe('parseValidationRule', () => {
  it('checkbox always validates regardless of `validate`', () => {
    const { rule, diagnostic } = parseValidationRule('checkbox', 'ignored', null, null, 'VERBOSE')
    expect(rule).toEqual({ kind: 'always' })
    expect(diagnostic).toBeNull()
  })

  it('number becomes a numericRange from min/max, ignoring `validate`', () => {
    const { rule } = parseValidationRule('number', 'ignored', 1, 10, 'TOP_N')
    expect(rule).toEqual({ kind: 'numericRange', min: 1, max: 10 })
  })

  it('select always becomes nonEmpty', () => {
    const { rule } = parseValidationRule('select', null, null, null, 'PLATFORM')
    expect(rule).toEqual({ kind: 'nonEmpty' })
  })

  it('text with no `validate` becomes nonEmpty', () => {
    expect(parseValidationRule('text', null, null, null, 'JOB_ID').rule).toEqual({ kind: 'nonEmpty' })
  })

  it('text with `validate: "nonempty"` becomes nonEmpty', () => {
    expect(parseValidationRule('text', 'nonempty', null, null, 'JOB_ID').rule).toEqual({
      kind: 'nonEmpty',
    })
  })

  it('text with any other `validate` becomes a regex rule', () => {
    const { rule, diagnostic } = parseValidationRule('text', '^[0-9a-f]{8}$', null, null, 'JOB_ID')
    expect(rule).toEqual({ kind: 'regex', source: '^[0-9a-f]{8}$', invalid: false })
    expect(diagnostic).toBeNull()
  })

  it('an uncompilable regex is marked invalid and reports exactly one diagnostic naming the variable (FR-PROMPT-004a)', () => {
    const { rule, diagnostic } = parseValidationRule('text', '([unclosed', null, null, 'JOB_ID')
    expect(rule).toEqual({ kind: 'regex', source: '([unclosed', invalid: true })
    expect(diagnostic).toBe('Invalid validate pattern for variable "JOB_ID"')
  })
})

describe('validateValue', () => {
  it('always is always valid', () => {
    expect(validateValue({ kind: 'always' }, boolean(false))).toBe(true)
  })

  it('nonEmpty rejects blank/whitespace-only text and accepts real content', () => {
    const rule = { kind: 'nonEmpty' } as const
    expect(validateValue(rule, text(''))).toBe(false)
    expect(validateValue(rule, text('   '))).toBe(false)
    expect(validateValue(rule, text('abc'))).toBe(true)
  })

  it('nonEmpty rejects an empty select and accepts a chosen option', () => {
    const rule = { kind: 'nonEmpty' } as const
    expect(validateValue(rule, select(''))).toBe(false)
    expect(validateValue(rule, select('linux'))).toBe(true)
  })

  it('an invalid regex rule never matches, even a value that "looks" valid', () => {
    const rule = { kind: 'regex', source: '([unclosed', invalid: true } as const
    expect(validateValue(rule, text('anything'))).toBe(false)
  })

  it('a valid regex rule matches on the trimmed value', () => {
    const rule = { kind: 'regex', source: '^[0-9a-f]{8}$', invalid: false } as const
    expect(validateValue(rule, text('  1a2b3c4d  '))).toBe(true)
    expect(validateValue(rule, text('not-hex'))).toBe(false)
  })

  it('numericRange rejects non-finite and out-of-range values, accepts in-range', () => {
    const rule = { kind: 'numericRange', min: 1, max: 10 } as const
    expect(validateValue(rule, number(Number.NaN))).toBe(false)
    expect(validateValue(rule, number(0))).toBe(false)
    expect(validateValue(rule, number(11))).toBe(false)
    expect(validateValue(rule, number(5))).toBe(true)
  })

  it('numericRange with no min/max only rejects non-finite values', () => {
    const rule = { kind: 'numericRange', min: null, max: null } as const
    expect(validateValue(rule, number(Number.NaN))).toBe(false)
    expect(validateValue(rule, number(-999))).toBe(true)
  })

  it('a rule/value kind mismatch is invalid rather than throwing', () => {
    expect(validateValue({ kind: 'numericRange', min: null, max: null }, text('5'))).toBe(false)
    expect(validateValue({ kind: 'regex', source: '.*', invalid: false }, select('x'))).toBe(false)
  })
})
