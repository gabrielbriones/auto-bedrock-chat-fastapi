import { describe, expect, it } from '@jest/globals'

import { allRequiredVariableNames, parsePromptCatalog } from '@/domains/prompt-catalog/domain/prompt-catalog'

describe('parsePromptCatalog', () => {
  it('parses presets and variables and cross-links required variables to their definitions', () => {
    const catalog = parsePromptCatalog(
      [
        {
          id: 'workload-analysis',
          label: 'Workload Analysis',
          description: 'Full CPU workload characterization report',
          template: 'JOB_ID = {{JOB_ID}}',
        },
      ],
      [{ name: 'JOB_ID', label: 'Job ID', input_type: 'text', validate: 'nonempty' }],
    )

    expect(catalog.presets).toHaveLength(1)
    expect(catalog.presets[0]?.requiredVariables).toEqual(['JOB_ID'])
    expect(catalog.variables.JOB_ID?.label).toBe('Job ID')
    expect(catalog.diagnostics).toEqual([])
  })

  it('infers a plain required text field for a placeholder with no variables: entry', () => {
    const catalog = parsePromptCatalog(
      [
        {
          id: 'tenant-report',
          label: 'Tenant Report',
          description: '',
          template: 'Generate a full report for tenant {{TENANT}}.',
        },
      ],
      [],
    )

    expect(catalog.variables.TENANT).toEqual({
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

  it('drops a malformed (nameless) variable entry with a diagnostic instead of throwing', () => {
    const catalog = parsePromptCatalog(
      [],
      [{ label: 'Orphaned, no name' }, { name: 'JOB_ID' }],
    )

    expect(Object.keys(catalog.variables)).toEqual(['JOB_ID'])
  })

  it('an empty catalogue parses to no presets and no variables (FR-PROMPT-011)', () => {
    const catalog = parsePromptCatalog([], [])
    expect(catalog).toEqual({ presets: [], variables: {}, diagnostics: [] })
  })

  // Mirrors `workload_analyzer/prompts.yaml` (the real backend catalogue) as of this ticket:
  // two declared variables (JOB_ID, NEW_JOB_ID) and four presets, one of which (Regression Check)
  // requires both. Templates are trimmed to the parts that matter for parsing.
  it('parses the real backend prompts.yaml shape end to end', () => {
    const catalog = parsePromptCatalog(
      [
        {
          id: 'workload-analysis',
          label: 'Workload Analysis',
          description: 'Full CPU workload characterization report\n(IPC, memory, instruction mix, classification)',
          template: 'JOB_ID = {{JOB_ID}}\n\nYou are a senior CPU performance engineer...',
        },
        {
          id: 'bottleneck-suggestion',
          label: 'Bottleneck Suggestion',
          description: 'Suggest the primary bottleneck (compute, memory, bandwidth) for a given workload based on IPC and MPKI metrics.',
          template: 'JOB_ID = {{JOB_ID}}\n\nYou are a performance engineer diagnosing bottlenecks from IWPS data.',
        },
        {
          id: 'confidence-check',
          label: 'Confidence Check',
          description: 'Validate whether the IWPS simulation result is trustworthy before deeper analysis.',
          template: 'JOB_ID = {{JOB_ID}}\n\nYou are a CPU performance engineer validating the reliability of IWPS simulation results.',
        },
        {
          id: 'regression-check',
          label: 'Regression Check',
          description: 'Compare two IWPS jobs to determine performance improvement or regression.',
          template: 'BASE_JOB = {{JOB_ID}}\nNEW_JOB = {{NEW_JOB_ID}}\n\nDetermine if NEW_JOB improved or regressed vs BASE_JOB.',
        },
      ],
      [
        {
          name: 'JOB_ID',
          label: 'Job ID',
          input_type: 'text',
          validate: '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
          placeholder: 'e.g. e62f2481-b56e-4c9f-b077-3f013ea6b796',
        },
        {
          name: 'NEW_JOB_ID',
          label: 'New Job ID',
          input_type: 'text',
          validate: '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
          placeholder: 'e.g. 2481e62f-b56e-4c9f-b077-3f013ea6b796',
        },
      ],
    )

    expect(catalog.presets.map((preset) => preset.id)).toEqual([
      'workload-analysis',
      'bottleneck-suggestion',
      'confidence-check',
      'regression-check',
    ])
    expect(catalog.presets.map((preset) => preset.requiredVariables)).toEqual([
      ['JOB_ID'],
      ['JOB_ID'],
      ['JOB_ID'],
      ['JOB_ID', 'NEW_JOB_ID'],
    ])
    expect(Object.keys(catalog.variables)).toEqual(['JOB_ID', 'NEW_JOB_ID'])
    expect(catalog.variables.JOB_ID?.validationRule).toEqual({
      kind: 'regex',
      source: '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
      invalid: false,
    })
    expect(catalog.diagnostics).toEqual([])
  })
})

describe('allRequiredVariableNames', () => {
  it('returns the union across every preset, unique and in first-occurrence order', () => {
    const catalog = parsePromptCatalog(
      [
        { id: 'a', label: 'A', description: '', template: '{{JOB_ID}}' },
        { id: 'b', label: 'B', description: '', template: '{{JOB_ID}} {{NEW_JOB_ID}}' },
      ],
      [],
    )

    expect(allRequiredVariableNames(catalog)).toEqual(['JOB_ID', 'NEW_JOB_ID'])
  })

  it('is empty for a catalogue with no presets', () => {
    expect(allRequiredVariableNames(parsePromptCatalog([], []))).toEqual([])
  })
})
