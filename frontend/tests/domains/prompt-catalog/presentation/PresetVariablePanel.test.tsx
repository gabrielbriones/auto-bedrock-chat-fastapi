import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'

import { allRequiredVariableNames, parsePromptCatalog } from '@/domains/prompt-catalog/domain/prompt-catalog'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
import { defaultBindingFor } from '@/domains/prompt-catalog/domain/prompt-variable'
import { PresetVariablePanel } from '@/domains/prompt-catalog/presentation/PresetVariablePanel'

const catalog = parsePromptCatalog(
  [
    {
      id: 'workload-analysis',
      label: 'Workload Analysis',
      description: '',
      template: 'JOB_ID = {{JOB_ID}}',
    },
    {
      id: 'health-check',
      label: 'Health Check',
      description: '',
      template: 'Please summarise the current API health status.',
    },
  ],
  [{ name: 'JOB_ID', label: 'Job ID', input_type: 'text', validate: 'nonempty' }],
)

const bindingsFor = (presetId: string): Record<string, VariableValue> => {
  const preset = catalog.presets.find((candidate) => candidate.id === presetId)
  const bindings: Record<string, VariableValue> = {}
  for (const name of preset?.requiredVariables ?? []) {
    const variable = catalog.variables[name]
    if (variable !== undefined) {
      bindings[name] = defaultBindingFor(variable)
    }
  }
  return bindings
}

const comparisonCatalog = parsePromptCatalog(
  [
    { id: 'analysis', label: 'Analysis - Single', group: 'Analysis', description: '', template: '{{JOB_ID}}' },
    { id: 'compare', label: 'Validation - Compare', group: 'Validation', description: '', template: '{{JOB_ID}} {{NEW_JOB_ID}}' },
  ],
  [
    { name: 'JOB_ID', label: 'Job ID', input_type: 'text' },
    { name: 'NEW_JOB_ID', label: 'New Job ID', input_type: 'text' },
  ],
)

describe('PresetVariablePanel', () => {
  it('renders one field per required variable', () => {
    const preset = catalog.presets[0]
    if (preset === undefined) throw new Error('fixture preset missing')

    render(
      <PresetVariablePanel
        variableNames={preset.requiredVariables}
        variables={catalog.variables}
        bindings={bindingsFor('workload-analysis')}
        onBindingChange={vi.fn()}
      />,
    )

    expect(screen.getByLabelText('Job ID')).toBeInTheDocument()
  })

  it('keeps both job fields visible when a grouped comparison preset requires them', () => {
    const bindings = Object.fromEntries(
      Object.values(comparisonCatalog.variables).map((variable) => [variable.name, defaultBindingFor(variable)]),
    )

    render(
      <PresetVariablePanel
        variableNames={allRequiredVariableNames(comparisonCatalog)}
        variables={comparisonCatalog.variables}
        bindings={bindings}
        onBindingChange={vi.fn()}
      />,
    )

    expect(screen.getByLabelText('Job ID')).toBeInTheDocument()
    expect(screen.getByLabelText('New Job ID')).toBeInTheDocument()
  })

  it('renders nothing at all for an empty variableNames list (FR-PROMPT-011 parity)', () => {
    const { container } = render(
      <PresetVariablePanel
        variableNames={[]}
        variables={catalog.variables}
        bindings={{}}
        onBindingChange={vi.fn()}
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('shows the detected badge for a name in the detected set', () => {
    const preset = catalog.presets[0]
    if (preset === undefined) throw new Error('fixture preset missing')

    render(
      <PresetVariablePanel
        variableNames={preset.requiredVariables}
        variables={catalog.variables}
        bindings={bindingsFor('workload-analysis')}
        detected={new Set(['JOB_ID'])}
        onBindingChange={vi.fn()}
      />,
    )

    expect(screen.getByText('Detected')).toBeInTheDocument()
  })

  it('does not report a required-field error after leaving the field', async () => {
    const user = userEvent.setup()
    const preset = catalog.presets[0]
    if (preset === undefined) throw new Error('fixture preset missing')

    render(
      <PresetVariablePanel
        variableNames={preset.requiredVariables}
        variables={catalog.variables}
        bindings={bindingsFor('workload-analysis')}
        onBindingChange={vi.fn()}
      />,
    )

    const field = screen.getByLabelText('Job ID')
    expect(field).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await user.click(field)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await user.tab()

    expect(field).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reports a required-field error after preset activation requests validation', () => {
    const preset = catalog.presets[0]
    if (preset === undefined) throw new Error('fixture preset missing')

    render(
      <PresetVariablePanel
        variableNames={preset.requiredVariables}
        variables={catalog.variables}
        bindings={bindingsFor('workload-analysis')}
        showValidationErrors
        onBindingChange={vi.fn()}
      />,
    )

    const field = screen.getByLabelText('Job ID')
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('alert')).toHaveTextContent('This field is required.')
  })

  it('routes a field change back through onBindingChange keyed by variable name', async () => {
    const preset = catalog.presets[0]
    if (preset === undefined) throw new Error('fixture preset missing')
    const onBindingChange = vi.fn<(name: string, value: VariableValue) => void>()
    const user = userEvent.setup()

    render(
      <PresetVariablePanel
        variableNames={preset.requiredVariables}
        variables={catalog.variables}
        bindings={bindingsFor('workload-analysis')}
        onBindingChange={onBindingChange}
      />,
    )

    await user.type(screen.getByLabelText('Job ID'), 'a')

    expect(onBindingChange).toHaveBeenCalledWith('JOB_ID', { kind: 'text', value: 'a' })
  })

  it('has no axe violations', async () => {
    const preset = catalog.presets[0]
    if (preset === undefined) throw new Error('fixture preset missing')

    const { container } = render(
      <PresetVariablePanel
        variableNames={preset.requiredVariables}
        variables={catalog.variables}
        bindings={bindingsFor('workload-analysis')}
        onBindingChange={vi.fn()}
      />,
    )

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
