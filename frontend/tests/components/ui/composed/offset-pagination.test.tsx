import { describe, expect, it, jest } from '@jest/globals'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { OffsetPagination } from '@/components/ui/composed/offset-pagination'
import { ADMIN_COPY } from '@/shared/copy/admin'
import { offsetWindow } from '@/shared/kernel/pagination'

const renderPagination = (offset: number, total: number, onNavigate = jest.fn()) => {
  render(<OffsetPagination range={offsetWindow({ limit: 50, offset }, total)} onNavigate={onNavigate} />)
  return onNavigate
}

const previous = () => screen.getByRole('button', { name: ADMIN_COPY.pagination.previous })
const next = () => screen.getByRole('button', { name: ADMIN_COPY.pagination.next })

describe('OffsetPagination', () => {
  it('states the range it is showing', () => {
    renderPagination(0, 128)

    expect(screen.getByText(ADMIN_COPY.pagination.range(1, 50, 128))).toBeInTheDocument()
  })

  it('navigates by page', async () => {
    const user = userEvent.setup()
    const onNavigate = renderPagination(50, 128)

    await user.click(next())
    expect(onNavigate).toHaveBeenCalledWith(100)

    await user.click(previous())
    expect(onNavigate).toHaveBeenCalledWith(0)
  })

  it('disables navigation that would leave the list', () => {
    renderPagination(0, 12)

    expect(previous()).toBeDisabled()
    expect(next()).toBeDisabled()
  })

  it('renders nothing only when there is genuinely nowhere to go', () => {
    const { container } = render(
      <OffsetPagination range={offsetWindow({ limit: 50, offset: 0 }, 0)} onNavigate={() => {}} />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('is a named landmark so it can be reached directly', () => {
    renderPagination(0, 128)

    expect(screen.getByRole('navigation', { name: ADMIN_COPY.pagination.label })).toBeInTheDocument()
  })
})
