import { describe, expect, it } from '@jest/globals'

import {
  bindingFromText,
  defaultBindingFor,
  inferPromptVariable,
  parsePromptVariable,
} from '@/domains/prompt-catalog/domain/prompt-variable'

describe('parsePromptVariable', () => {
  it('parses a minimal text variable, deriving its label and defaulting to nonEmpty (real prompts.yaml shape)', () => {
    const parsed = parsePromptVariable({
      name: 'JOB_ID',
      label: 'Job ID',
      input_type: 'text',
      validate: '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
      placeholder: 'e.g. e62f2481-b56e-4c9f-b077-3f013ea6b796',
    })

    expect(parsed?.variable).toEqual({
      name: 'JOB_ID',
      label: 'Job ID',
      inputType: 'text',
      placeholder: 'e.g. e62f2481-b56e-4c9f-b077-3f013ea6b796',
      defaultValue: null,
      options: [],
      min: null,
      max: null,
      step: null,
      validationRule: {
        kind: 'regex',
        source: '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        invalid: false,
      },
      detection: {
        pattern: '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        flags: 'i',
      },
    })
    expect(parsed?.diagnostics).toEqual([])
  })

  it('derives the label when none is supplied (FR-PROMPT-003)', () => {
    const parsed = parsePromptVariable({ name: 'NEW_JOB_ID' })
    expect(parsed?.variable.label).toBe('New Job Id')
  })

  it('degrades an unknown input_type to text', () => {
    const parsed = parsePromptVariable({ name: 'WHEN', input_type: 'date' })
    expect(parsed?.variable.inputType).toBe('text')
  })

  it('parses select options given as bare strings and as {value,label} records', () => {
    const parsed = parsePromptVariable({
      name: 'PLATFORM',
      input_type: 'select',
      options: ['linux', { value: 'windows', label: 'Windows' }],
    })

    expect(parsed?.variable.options).toEqual([
      { value: 'linux', label: 'linux' },
      { value: 'windows', label: 'Windows' },
    ])
  })

  it('drops a malformed option entry rather than throwing', () => {
    const parsed = parsePromptVariable({ name: 'PLATFORM', input_type: 'select', options: [42, null] })
    expect(parsed?.variable.options).toEqual([])
  })

  it('parses a number variable with min/max/step', () => {
    const parsed = parsePromptVariable({ name: 'TOP_N', input_type: 'number', min: 1, max: 10, step: 1 })
    expect(parsed?.variable.validationRule).toEqual({ kind: 'numericRange', min: 1, max: 10 })
    expect(parsed?.variable.step).toBe(1)
  })

  it('bubbles a parse diagnostic for an uncompilable validate regex (FR-PROMPT-004a)', () => {
    const parsed = parsePromptVariable({ name: 'JOB_ID', validate: '([unclosed' })
    expect(parsed?.diagnostics).toEqual(['Invalid validate pattern for variable "JOB_ID"'])
  })

  it('returns null for a config with no usable name, rather than a nameless field', () => {
    expect(parsePromptVariable({})).toBeNull()
    expect(parsePromptVariable({ name: '' })).toBeNull()
    expect(parsePromptVariable({ name: 42 })).toBeNull()
  })

  it('ignores wrong-typed fields defensively instead of throwing', () => {
    const parsed = parsePromptVariable({ name: 'JOB_ID', min: 'one', options: 'not-an-array' })
    expect(parsed?.variable.min).toBeNull()
    expect(parsed?.variable.options).toEqual([])
  })
})

describe('inferPromptVariable', () => {
  it('produces a plain required text field for an undeclared placeholder', () => {
    expect(inferPromptVariable('TENANT')).toEqual({
      name: 'TENANT',
      label: 'Tenant',
      inputType: 'text',
      placeholder: null,
      defaultValue: null,
      options: [],
      min: null,
      max: null,
      step: null,
      validationRule: { kind: 'nonEmpty' },
      detection: null,
    })
  })
})

describe('defaultBindingFor', () => {
  it('binds checkbox from the string default "true"', () => {
    const variable = { ...inferPromptVariable('VERBOSE'), inputType: 'checkbox' as const, defaultValue: 'true' }
    expect(defaultBindingFor(variable)).toEqual({ kind: 'boolean', value: true })
  })

  it('binds an unset checkbox default to false', () => {
    const variable = { ...inferPromptVariable('VERBOSE'), inputType: 'checkbox' as const }
    expect(defaultBindingFor(variable)).toEqual({ kind: 'boolean', value: false })
  })

  it('binds number from its default, or NaN when unset', () => {
    const withDefault = { ...inferPromptVariable('TOP_N'), inputType: 'number' as const, defaultValue: '5' }
    expect(defaultBindingFor(withDefault)).toEqual({ kind: 'number', value: 5 })

    const withoutDefault = { ...inferPromptVariable('TOP_N'), inputType: 'number' as const }
    expect(defaultBindingFor(withoutDefault).kind).toBe('number')
    expect(Number.isNaN((defaultBindingFor(withoutDefault) as { value: number }).value)).toBe(true)
  })

  it('binds select/text to their default or empty string', () => {
    expect(defaultBindingFor(inferPromptVariable('JOB_ID'))).toEqual({ kind: 'text', value: '' })

    const select = { ...inferPromptVariable('PLATFORM'), inputType: 'select' as const, defaultValue: 'linux' }
    expect(defaultBindingFor(select)).toEqual({ kind: 'select', value: 'linux' })
  })
})

describe('bindingFromText', () => {
  it('accepts "true" or "1" for a checkbox, anything else is false', () => {
    const verbose = { ...inferPromptVariable('VERBOSE'), inputType: 'checkbox' as const }
    expect(bindingFromText(verbose, 'true')).toEqual({ kind: 'boolean', value: true })
    expect(bindingFromText(verbose, '1')).toEqual({ kind: 'boolean', value: true })
    expect(bindingFromText(verbose, 'yes')).toEqual({ kind: 'boolean', value: false })
  })

  it('converts a number variable from its raw string', () => {
    const topN = { ...inferPromptVariable('TOP_N'), inputType: 'number' as const }
    expect(bindingFromText(topN, '5')).toEqual({ kind: 'number', value: 5 })
  })

  it('passes select/text through as-is', () => {
    const platform = { ...inferPromptVariable('PLATFORM'), inputType: 'select' as const }
    expect(bindingFromText(platform, 'linux')).toEqual({ kind: 'select', value: 'linux' })
    expect(bindingFromText(inferPromptVariable('JOB_ID'), 'abc')).toEqual({ kind: 'text', value: 'abc' })
  })
})
