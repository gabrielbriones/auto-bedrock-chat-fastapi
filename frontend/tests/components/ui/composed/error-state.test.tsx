import { describe, expect, it } from '@jest/globals'
import { render, screen } from '@testing-library/react'

import { ErrorState, buildSha } from '@/components/ui/composed/error-state'
import { SHELL } from '@/shared/copy/shell'

describe('ErrorState', () => {
  it('names the build so a screenshot identifies what was running', () => {
    render(<ErrorState description="It broke." />)

    expect(screen.getByText(SHELL.error.reference(buildSha()))).toBeInTheDocument()
  })

  it('carries a diagnostic reference alongside the build when one is given', () => {
    render(<ErrorState description="It broke." reference="abc-123" />)

    expect(screen.getByText(`Reference: abc-123 · ${buildSha()}`)).toBeInTheDocument()
  })

  it('offers no retry action when there is nothing to retry', () => {
    render(<ErrorState description="It broke." />)

    expect(screen.queryByRole('button', { name: SHELL.error.retry })).not.toBeInTheDocument()
  })

  it('announces itself as an alert', () => {
    render(<ErrorState description="It broke." />)

    expect(screen.getByRole('alert')).toHaveTextContent('It broke.')
  })
})
