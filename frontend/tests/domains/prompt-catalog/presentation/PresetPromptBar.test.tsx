import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, jest } from '@jest/globals'
import { axe } from 'jest-axe'

import { parsePromptCatalog } from '@/domains/prompt-catalog/domain/prompt-catalog'
import { defaultBindingFor } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
import { PresetPromptBar } from '@/domains/prompt-catalog/presentation/PresetPromptBar'

const catalog = parsePromptCatalog(
  [
    {
      id: 'workload-analysis',
      label: 'Workload Analysis',
      description: 'Full CPU workload characterization',
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

const defaultBindings = (): Record<string, VariableValue> =>
  Object.fromEntries(Object.values(catalog.variables).map((variable) => [variable.name, defaultBindingFor(variable)]))

const groupedCatalog = parsePromptCatalog(
  [
    { id: 'a-one', label: 'Category A - One', group: 'Category A', description: '', template: 'one' },
    { id: 'a-two', label: 'Category A - Two', group: 'Category A', description: '', template: 'two' },
    { id: 'b-one', label: 'Category B item', group: 'Category B', description: '', template: 'three' },
    { id: 'c-one', label: 'Category C item', group: 'Category C', description: '', template: 'four' },
  ],
  [],
)

describe('PresetPromptBar', () => {
  it('renders every preset label', () => {
    render(<PresetPromptBar catalog={catalog} bindings={defaultBindings()} locked={false} lockedReason="" onActivate={jest.fn()} />)

    expect(screen.getByRole('button', { name: /Workload Analysis/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Health Check/ })).toBeInTheDocument()
  })

  it('renders source-defined groups as columns and strips their repeated label prefix', () => {
    const { container } = render(
      <PresetPromptBar catalog={groupedCatalog} bindings={{}} locked={false} lockedReason="" onActivate={jest.fn()} />,
    )

    expect(screen.getByRole('heading', { name: 'Category A' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Category B' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Category C' })).toBeInTheDocument()
    expect(screen.getByText('One')).toBeInTheDocument()
    expect(screen.queryByText('Category A - One')).not.toBeInTheDocument()
    expect(container.querySelector('.sm\\:grid-cols-3')).toBeInTheDocument()
  })

  it('keeps catalogs without group metadata in the original flat layout', () => {
    render(<PresetPromptBar catalog={catalog} bindings={defaultBindings()} locked={false} lockedReason="" onActivate={jest.fn()} />)

    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument()
  })

  it('renders nothing at all for an empty catalogue (FR-PROMPT-011)', () => {
    const { container } = render(
      <PresetPromptBar catalog={parsePromptCatalog([], [])} bindings={{}} locked={false} lockedReason="" onActivate={jest.fn()} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('disables a preset whose required variable is missing/invalid, stating why, and enables it once it validates (FR-PROMPT-005/012)', () => {
    const { rerender } = render(
      <PresetPromptBar catalog={catalog} bindings={defaultBindings()} locked={false} lockedReason="" onActivate={jest.fn()} />,
    )

    const button = screen.getByRole('button', { name: /Workload Analysis/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription(expect.stringContaining('Job ID'))

    rerender(
      <PresetPromptBar
        catalog={catalog}
        bindings={{ JOB_ID: { kind: 'text', value: 'abc' } }}
        locked={false}
        lockedReason=""
        onActivate={jest.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /Workload Analysis/ })).toBeEnabled()
  })

  it('keeps disabled reasons out of the layout and reveals them on hover', async () => {
    const user = userEvent.setup()
    render(<PresetPromptBar catalog={catalog} bindings={defaultBindings()} locked={false} lockedReason="" onActivate={jest.fn()} />)

    const button = screen.getByRole('button', { name: 'Workload Analysis' })
    expect(screen.getByText('Missing or invalid: Job ID.')).toHaveClass('sr-only')
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    await user.hover(button.parentElement!)

    expect(await screen.findByRole('tooltip')).toHaveTextContent('Missing or invalid: Job ID.')
  })

  it('locked disables every preset uniformly with the locked reason, even a valid one', () => {
    render(
      <PresetPromptBar
        catalog={catalog}
        bindings={{ JOB_ID: { kind: 'text', value: 'abc' } }}
        locked
        lockedReason="Offline"
        onActivate={jest.fn()}
      />,
    )

    const button = screen.getByRole('button', { name: /Workload Analysis/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription(expect.stringContaining('Offline'))
  })

  it('calls onActivate with the preset id when clicked', async () => {
    const onActivate = jest.fn()
    const user = userEvent.setup()

    render(
      <PresetPromptBar
        catalog={catalog}
        bindings={{ JOB_ID: { kind: 'text', value: 'abc' } }}
        locked={false}
        lockedReason=""
        onActivate={onActivate}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Workload Analysis/ }))

    expect(onActivate).toHaveBeenCalledWith('workload-analysis')
  })

  it('has no axe violations', async () => {
    const { container } = render(
      <PresetPromptBar catalog={catalog} bindings={defaultBindings()} locked={false} lockedReason="" onActivate={jest.fn()} />,
    )

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
