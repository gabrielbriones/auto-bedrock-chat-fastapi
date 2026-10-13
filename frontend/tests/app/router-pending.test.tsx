import { describe, expect, it } from '@jest/globals'
import { render, screen } from '@testing-library/react'

import { RoutePending } from '@/app/route-pending'
import { SHELL } from '@/shared/copy/shell'

describe('RoutePending', () => {
  it('shows a compact, announced loading state instead of page-wide rows', () => {
    const { container } = render(<RoutePending />)

    expect(screen.getByRole('status')).toHaveTextContent(SHELL.loading)
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(1)
    expect(screen.getByText(SHELL.loading).parentElement).toHaveClass('max-w-xs')
  })
})