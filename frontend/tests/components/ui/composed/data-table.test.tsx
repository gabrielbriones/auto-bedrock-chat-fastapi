import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'vitest-axe'

import { DataTable, type DataTableColumn } from '@/components/ui/composed/data-table'
import { ADMIN_COPY } from '@/shared/copy/admin'

type Row = { readonly id: string; readonly user: string; readonly query: string }

const rows: readonly Row[] = [
  { id: '1', user: 'rzhang', query: 'Why is the vectorization ratio low?' },
  { id: '2', user: 'mokoye', query: 'Summarise the EMON counters.' },
]

const columns: readonly DataTableColumn<Row>[] = [
  { id: 'query', header: 'Query', cell: (row) => row.query },
  { id: 'user', header: 'User', cell: (row) => row.user },
]

const renderTable = (props: Partial<Parameters<typeof DataTable<Row>>[0]> = {}) =>
  render(
    <DataTable
      caption="Feedback queue"
      columns={columns}
      rows={rows}
      rowKey={(row) => row.id}
      emptyTitle="No feedback entries match the current filters."
      {...props}
    />,
  )

describe('DataTable', () => {
  it('renders a header and a row per record', () => {
    renderTable()

    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByRole('columnheader', { name: 'Query' })).toBeInTheDocument()
  })

  // FIX-13: the legacy table hung a click handler on a bare `<tr>`, unreachable by keyboard.
  it('exposes each row action as a button carrying the row name', () => {
    renderTable({ rowAction: { label: (row) => row.query, onActivate: () => {} } })

    expect(
      screen.getByRole('button', { name: ADMIN_COPY.table.open(rows[0]!.query) }),
    ).toBeInTheDocument()
  })

  it('activates a row from the keyboard', async () => {
    const onActivate = vi.fn()
    const user = userEvent.setup()
    renderTable({ rowAction: { label: (row) => row.query, onActivate } })

    await user.tab()
    expect(screen.getByRole('button', { name: ADMIN_COPY.table.open(rows[0]!.query) })).toHaveFocus()

    await user.keyboard('{Enter}')
    expect(onActivate).toHaveBeenCalledWith(rows[0])

    await user.keyboard(' ')
    expect(onActivate).toHaveBeenCalledTimes(2)
  })

  it('gives a row exactly one tab stop even though the whole row is clickable', async () => {
    const onActivate = vi.fn()
    const user = userEvent.setup()
    renderTable({ rowAction: { label: (row) => row.query, onActivate } })

    await user.click(screen.getByRole('button', { name: ADMIN_COPY.table.open(rows[0]!.query) }))

    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it('states an empty result rather than rendering an empty body', () => {
    renderTable({ rows: [], emptyTitle: 'No feedback entries match the current filters.' })

    expect(screen.getByText('No feedback entries match the current filters.')).toBeInTheDocument()
  })

  it('keeps loading, error and empty distinct', () => {
    const { rerender } = renderTable({ rows: [], isLoading: true })
    expect(screen.getByRole('status')).toBeInTheDocument()

    rerender(
      <DataTable
        caption="Feedback queue"
        columns={columns}
        rows={[]}
        rowKey={(row) => row.id}
        emptyTitle="No feedback entries match the current filters."
        error={<p role="alert">Could not load feedback.</p>}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load feedback.')
    expect(screen.queryByText('No feedback entries match the current filters.')).not.toBeInTheDocument()
  })

  it('marks the selected rows for assistive tech', () => {
    renderTable({ selectedKeys: new Set(['2']) })

    const selected = screen.getAllByRole('row').filter((row) => row.dataset.state === 'selected')
    expect(selected).toHaveLength(1)
  })

  it('has no accessibility violations', async () => {
    const { container } = renderTable({ rowAction: { label: (row) => row.query, onActivate: () => {} } })
    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
