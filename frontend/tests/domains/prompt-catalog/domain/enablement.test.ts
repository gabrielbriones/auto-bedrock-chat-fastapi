import { describe, expect, it } from 'vitest'

import { evaluatePreset } from '@/domains/prompt-catalog/domain/enablement'
import { inferPromptVariable, type PromptVariable } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { PresetPrompt } from '@/domains/prompt-catalog/domain/preset-prompt'

const preset: PresetPrompt = {
  id: 'workload-analysis',
  label: 'Workload Analysis',
  displayLabel: 'Workload Analysis',
  group: null,
  description: '',
  template: '{{JOB_ID}}',
  requiredVariables: ['JOB_ID'],
}

const jobId: PromptVariable = inferPromptVariable('JOB_ID')

describe('evaluatePreset', () => {
  it('is disabled with the variable named as missing when unbound', () => {
    const evaluation = evaluatePreset(preset, { JOB_ID: jobId }, {})
    expect(evaluation).toEqual({ enabled: false, missing: ['JOB_ID'], invalid: [] })
  })

  it('is disabled with the variable named as invalid when bound but failing validation', () => {
    const evaluation = evaluatePreset(preset, { JOB_ID: jobId }, { JOB_ID: { kind: 'text', value: '' } })
    expect(evaluation).toEqual({ enabled: false, missing: [], invalid: ['JOB_ID'] })
  })

  it('is enabled when every required variable validates', () => {
    const evaluation = evaluatePreset(preset, { JOB_ID: jobId }, { JOB_ID: { kind: 'text', value: 'abc' } })
    expect(evaluation).toEqual({ enabled: true, missing: [], invalid: [] })
  })

  it('is enabled with no required variables at all', () => {
    const empty: PresetPrompt = { ...preset, requiredVariables: [] }
    expect(evaluatePreset(empty, {}, {}).enabled).toBe(true)
  })

  it('numeric range boundaries (0/11/5 against min 1 max 10)', () => {
    const topN: PromptVariable = { ...jobId, name: 'TOP_N', inputType: 'number', validationRule: { kind: 'numericRange', min: 1, max: 10 } }
    const withTopN: PresetPrompt = { ...preset, requiredVariables: ['TOP_N'] }

    expect(evaluatePreset(withTopN, { TOP_N: topN }, { TOP_N: { kind: 'number', value: 0 } }).enabled).toBe(false)
    expect(evaluatePreset(withTopN, { TOP_N: topN }, { TOP_N: { kind: 'number', value: 11 } }).enabled).toBe(false)
    expect(evaluatePreset(withTopN, { TOP_N: topN }, { TOP_N: { kind: 'number', value: 5 } }).enabled).toBe(true)
  })

  it('names every failing variable, not just the first', () => {
    const newJobId: PromptVariable = inferPromptVariable('NEW_JOB_ID')
    const both: PresetPrompt = { ...preset, requiredVariables: ['JOB_ID', 'NEW_JOB_ID'] }

    const evaluation = evaluatePreset(both, { JOB_ID: jobId, NEW_JOB_ID: newJobId }, {
      JOB_ID: { kind: 'text', value: '' },
      NEW_JOB_ID: { kind: 'text', value: '' },
    })

    expect(evaluation).toEqual({ enabled: false, missing: [], invalid: ['JOB_ID', 'NEW_JOB_ID'] })
  })
})
