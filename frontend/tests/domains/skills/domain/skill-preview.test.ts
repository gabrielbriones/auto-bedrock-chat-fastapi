import { describe, expect, it } from '@jest/globals'

import { renderPreview, sampleValue, type SkillParameter } from '@/domains/skills/domain/public'

const parameter = (overrides: Partial<SkillParameter> & { readonly name: string }): SkillParameter => ({
  label: '',
  help: '',
  required: false,
  default: null,
  inputType: 'text',
  options: [],
  placeholder: '',
  example: '',
  ...overrides,
})

describe('sampleValue', () => {
  it.each([
    [{ name: 'a', example: 'ex', default: 'def', placeholder: 'ph', label: 'Label' }, 'ex'],
    [{ name: 'a', default: 'def', placeholder: 'ph', label: 'Label' }, 'def'],
    [{ name: 'a', placeholder: 'ph', label: 'Label' }, 'ph'],
    [{ name: 'a', options: ['first', 'second'], label: 'Label' }, 'first'],
    [{ name: 'a', label: 'Label' }, 'Label'],
    [{ name: 'a' }, 'a'],
  ])('prefers the most realistic hint (%o)', (overrides, expected) => {
    expect(sampleValue(parameter(overrides))).toBe(expected)
  })
})

describe('renderPreview', () => {
  it('substitutes declared parameters and keeps the text around them', () => {
    const segments = renderPreview('Investigate {{job_id}} now.', [parameter({ name: 'job_id', example: '42' })])

    expect(segments).toEqual([
      { kind: 'text', text: 'Investigate ' },
      { kind: 'value', name: 'job_id', text: '42' },
      { kind: 'text', text: ' now.' },
    ])
  })

  it('tolerates whitespace inside the braces and repeated placeholders', () => {
    const segments = renderPreview('{{ x }}+{{x}}', [parameter({ name: 'x', example: '1' })])

    expect(segments).toEqual([
      { kind: 'value', name: 'x', text: '1' },
      { kind: 'text', text: '+' },
      { kind: 'value', name: 'x', text: '1' },
    ])
  })

  it('flags a placeholder no parameter declares and keeps it verbatim', () => {
    expect(renderPreview('Use {{missing}}', [])).toEqual([
      { kind: 'text', text: 'Use ' },
      { kind: 'unknown', name: 'missing', text: '{{missing}}' },
    ])
  })

  it('returns markup-looking instructions untouched as text', () => {
    const text = '<img src=x onerror=alert(1)> {not a placeholder}'

    expect(renderPreview(text, [])).toEqual([{ kind: 'text', text }])
  })

  it('returns no segments for empty instructions', () => {
    expect(renderPreview('', [])).toEqual([])
  })
})
