import { describe, expect, it } from '@jest/globals'

import { extractRequiredVariables, parsePresetPrompt } from '@/domains/prompt-catalog/domain/preset-prompt'

describe('extractRequiredVariables', () => {
  it('finds a single placeholder', () => {
    expect(extractRequiredVariables('Analyze {{JOB_ID}}.')).toEqual(['JOB_ID'])
  })

  it('finds multiple distinct placeholders in supplied order', () => {
    expect(extractRequiredVariables('BASE_JOB = {{JOB_ID}}\nNEW_JOB = {{NEW_JOB_ID}}')).toEqual([
      'JOB_ID',
      'NEW_JOB_ID',
    ])
  })

  it('collapses repeated and adjacent placeholders to one entry', () => {
    expect(extractRequiredVariables('Analyze {{JOB_ID}} then re-check {{JOB_ID}}{{JOB_ID}}')).toEqual([
      'JOB_ID',
    ])
  })

  it('returns an empty list for a template with no placeholders', () => {
    expect(extractRequiredVariables('Please summarise the current API health status.')).toEqual([])
  })
})

describe('parsePresetPrompt', () => {
  it('parses id/label/description/template and derives requiredVariables', () => {
    const preset = parsePresetPrompt({
      id: 'workload-analysis',
      label: 'Workload Analysis',
      description: 'Full CPU workload characterization report',
      template: 'JOB_ID = {{JOB_ID}}\n...',
    })

    expect(preset).toEqual({
      id: 'workload-analysis',
      label: 'Workload Analysis',
      displayLabel: 'Workload Analysis',
      group: null,
      description: 'Full CPU workload characterization report',
      template: 'JOB_ID = {{JOB_ID}}\n...',
      requiredVariables: ['JOB_ID'],
    })
  })

  it('a preset with no placeholders parses with an empty requiredVariables list', () => {
    const preset = parsePresetPrompt({
      id: 'health-check',
      label: 'Health Check',
      description: 'Summarise API health',
      template: 'Please summarise the current API health status.',
    })

    expect(preset.requiredVariables).toEqual([])
  })

  it('normalizes a source-defined group and derives the label after its prefix', () => {
    const preset = parsePresetPrompt({
      id: 'analysis-iwps',
      label: 'Analysis - IWPS',
      group: '  Analysis  ',
      description: 'Analyze an IWPS job',
      template: 'Analyze {{JOB_ID}}',
    })

    expect(preset.group).toBe('Analysis')
    expect(preset.displayLabel).toBe('IWPS')
    expect(preset.label).toBe('Analysis - IWPS')
  })

  it('keeps the full label when it does not begin with the group', () => {
    const preset = parsePresetPrompt({
      id: 'confidence-check',
      label: 'Confidence Check',
      group: 'Validation & Comparison',
      description: 'Validate a result',
      template: 'Validate it',
    })

    expect(preset.displayLabel).toBe('Confidence Check')
  })
})
