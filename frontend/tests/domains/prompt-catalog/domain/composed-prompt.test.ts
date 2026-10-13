import { describe, expect, it } from '@jest/globals'

import { composePreset } from '@/domains/prompt-catalog/domain/composed-prompt'
import type { PresetPrompt } from '@/domains/prompt-catalog/domain/preset-prompt'
import { isErr, isOk } from '@/shared/kernel/result'

const preset: PresetPrompt = {
  id: 'workload-analysis',
  label: 'Workload Analysis',
  displayLabel: 'Workload Analysis',
  group: null,
  description: '',
  template: 'JOB_ID = {{JOB_ID}}',
  requiredVariables: ['JOB_ID'],
}

describe('composePreset', () => {
  it('substitutes every occurrence of a repeated placeholder (FR-PROMPT-006)', () => {
    const repeated: PresetPrompt = {
      ...preset,
      template: 'Analyze {{JOB_ID}} then re-check {{JOB_ID}}',
    }

    const result = composePreset(repeated, { JOB_ID: { kind: 'text', value: 'abc' } })

    expect(isOk(result) && result.value.text).toBe('Analyze abc then re-check abc')
  })

  it('substitutes verbatim, with no escaping/quoting/trimming (FR-PROMPT-006a)', () => {
    const result = composePreset(preset, { JOB_ID: { kind: 'text', value: '  <raw> & "quoted"  ' } })

    expect(isOk(result) && result.value.text).toBe('JOB_ID =   <raw> & "quoted"  ')
  })

  it('reports every missing binding and composes nothing (FR-PROMPT-014)', () => {
    const both: PresetPrompt = { ...preset, template: '{{JOB_ID}} {{NEW_JOB_ID}}', requiredVariables: ['JOB_ID', 'NEW_JOB_ID'] }

    const result = composePreset(both, {})

    expect(isErr(result) && result.error).toEqual({ kind: 'missing-variables', names: ['JOB_ID', 'NEW_JOB_ID'] })
  })

  it('a preset with no placeholders composes its template unchanged', () => {
    const plain: PresetPrompt = { ...preset, template: 'Static text.', requiredVariables: [] }

    const result = composePreset(plain, {})

    expect(isOk(result) && result.value).toEqual({ text: 'Static text.', presetId: 'workload-analysis', bindings: {} })
  })

  it('renders number/boolean bindings as their string form', () => {
    const numeric: PresetPrompt = { ...preset, template: 'N={{TOP_N}} V={{VERBOSE}}', requiredVariables: ['TOP_N', 'VERBOSE'] }

    const result = composePreset(numeric, {
      TOP_N: { kind: 'number', value: 5 },
      VERBOSE: { kind: 'boolean', value: true },
    })

    expect(isOk(result) && result.value.text).toBe('N=5 V=true')
  })

  it('carries only the bindings the preset actually used', () => {
    const result = composePreset(preset, {
      JOB_ID: { kind: 'text', value: 'abc' },
      UNRELATED: { kind: 'text', value: 'x' },
    })

    expect(isOk(result) && result.value.bindings).toEqual({ JOB_ID: { kind: 'text', value: 'abc' } })
  })
})
