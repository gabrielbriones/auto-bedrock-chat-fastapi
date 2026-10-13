import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, jest } from '@jest/globals'
import { axe } from 'jest-axe'

import { inferPromptVariable } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { PromptVariable } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
import { VariableField } from '@/domains/prompt-catalog/presentation/VariableField'

const jobId: PromptVariable = { ...inferPromptVariable('JOB_ID') }

describe('VariableField', () => {
  it('renders a text field and reports typed input', async () => {
    const onChange = jest.fn<(value: VariableValue) => void>()
    const user = userEvent.setup()

    render(<VariableField variable={jobId} value={{ kind: 'text', value: '' }} error={null} onChange={onChange} />)

    await user.type(screen.getByLabelText('Job Id'), 'a')

    expect(onChange).toHaveBeenCalledWith({ kind: 'text', value: 'a' })
  })

  it('associates an error message to the field via aria-describedby (FR-PROMPT accept: reported at the field)', () => {
    render(
      <VariableField
        variable={jobId}
        value={{ kind: 'text', value: '' }}
        error="This field is required."
        onChange={jest.fn()}
      />,
    )

    const field = screen.getByLabelText('Job Id')
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('alert')).toHaveTextContent('This field is required.')
    expect(field).toHaveAttribute('aria-describedby', screen.getByRole('alert').id)
  })

  it('renders no error element when valid', () => {
    render(<VariableField variable={jobId} value={{ kind: 'text', value: 'ok' }} error={null} onChange={jest.fn()} />)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders a number field honouring min/max/step and reports the numeric value', async () => {
    const onChange = jest.fn<(value: VariableValue) => void>()
    const user = userEvent.setup()
    const topN: PromptVariable = { ...jobId, name: 'TOP_N', label: 'Top N', inputType: 'number', min: 1, max: 10, step: 1 }

    render(<VariableField variable={topN} value={{ kind: 'number', value: Number.NaN }} error={null} onChange={onChange} />)

    const field = screen.getByLabelText('Top N')
    expect(field).toHaveAttribute('min', '1')
    expect(field).toHaveAttribute('max', '10')

    await user.type(field, '5')

    expect(onChange).toHaveBeenLastCalledWith({ kind: 'number', value: 5 })
  })

  it('renders a checkbox field and toggles independently', async () => {
    const onChange = jest.fn<(value: VariableValue) => void>()
    const user = userEvent.setup()
    const verbose: PromptVariable = { ...jobId, name: 'VERBOSE', label: 'Verbose', inputType: 'checkbox' }

    render(<VariableField variable={verbose} value={{ kind: 'boolean', value: false }} error={null} onChange={onChange} />)

    await user.click(screen.getByRole('checkbox', { name: 'Verbose' }))

    expect(onChange).toHaveBeenCalledWith({ kind: 'boolean', value: true })
  })

  it('renders a select field with its options and a placeholder when unset', async () => {
    const onChange = jest.fn<(value: VariableValue) => void>()
    const user = userEvent.setup()
    const platform: PromptVariable = {
      ...jobId,
      name: 'PLATFORM',
      label: 'Platform',
      inputType: 'select',
      options: [
        { value: 'linux', label: 'Linux' },
        { value: 'windows', label: 'Windows' },
      ],
    }

    render(<VariableField variable={platform} value={{ kind: 'select', value: '' }} error={null} onChange={onChange} />)

    expect(screen.getByText('Select Platform…')).toBeInTheDocument()

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'Windows' }))

    expect(onChange).toHaveBeenCalledWith({ kind: 'select', value: 'windows' })
  })

  it('has no axe violations for a text field with an error', async () => {
    const { container } = render(
      <VariableField
        variable={jobId}
        value={{ kind: 'text', value: '' }}
        error="This field is required."
        onChange={jest.fn()}
      />,
    )

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
