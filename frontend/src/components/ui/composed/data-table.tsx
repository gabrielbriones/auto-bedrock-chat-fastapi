import { useMemo, useState, type ReactNode } from 'react'
import { ArrowDownIcon, ArrowUpDownIcon, ArrowUpIcon } from 'lucide-react'

import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { EmptyState } from '@/components/ui/composed/empty-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { ADMIN_COPY } from '@/shared/copy/admin'
import { cn } from '@/lib/utils'

export type DataTableColumn<Row> = {
  readonly id: string
  readonly header: ReactNode
  readonly cell: (row: Row) => ReactNode
  readonly className?: string
  /** Present only on sortable columns. */
  readonly sortValue?: (row: Row) => string | number
}

type SortState = { readonly columnId: string; readonly direction: 'ascending' | 'descending' }

export type DataTableRowAction<Row> = {
  /** The accessible name of the row's control — what the reviewer is about to open. */
  readonly label: (row: Row) => string
  readonly onActivate: (row: Row) => void
}

export type DataTableProps<Row> = {
  readonly caption: string
  readonly columns: readonly DataTableColumn<Row>[]
  readonly rows: readonly Row[]
  readonly rowKey: (row: Row) => string
  readonly rowAction?: DataTableRowAction<Row>
  readonly selectedKeys?: ReadonlySet<string>
  readonly isLoading?: boolean
  readonly error?: ReactNode
  readonly emptyTitle: string
  readonly emptyDescription?: string
  readonly className?: string
  readonly tableClassName?: string
}

const stateRow = <Row,>(columns: readonly DataTableColumn<Row>[], content: ReactNode) => (
  <TableRow>
    <TableCell colSpan={columns.length} className="whitespace-normal p-0">
      {content}
    </TableCell>
  </TableRow>
)

// FIX-13. The legacy table hung a click handler on a bare `<tr>`, which no keyboard or screen
// reader could reach. The fix is *not* `role="button"` on the row — that would strip the row out
// of the table's required children and break the grid for assistive tech. Instead the first cell
// carries a real button holding the row's accessible name; pointer users still get the whole row
// as a target, and there is exactly one tab stop per row.
function ActionCell<Row>({
  row,
  action,
  children,
}: {
  readonly row: Row
  readonly action: DataTableRowAction<Row>
  readonly children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={ADMIN_COPY.table.open(action.label(row))}
      className="w-full cursor-pointer text-left underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      onClick={(event) => {
        // The row is also a pointer target; without this the handler would fire twice.
        event.stopPropagation()
        action.onActivate(row)
      }}
    >
      {children}
    </button>
  )
}

function BodyRow<Row>({
  row,
  columns,
  rowKey,
  rowAction,
  selectedKeys,
}: {
  readonly row: Row
  readonly columns: readonly DataTableColumn<Row>[]
  readonly rowKey: (row: Row) => string
  readonly rowAction: DataTableRowAction<Row> | undefined
  readonly selectedKeys: ReadonlySet<string> | undefined
}) {
  const selected = selectedKeys?.has(rowKey(row)) === true

  return (
    <TableRow
      {...(selected ? { 'data-state': 'selected' as const } : {})}
      className={cn(rowAction !== undefined && 'cursor-pointer')}
      onClick={
        rowAction === undefined
          ? undefined
          : () => {
              rowAction.onActivate(row)
            }
      }
    >
      {columns.map((column, index) => (
        <TableCell key={column.id} className={column.className}>
          {rowAction !== undefined && index === 0 ? (
            <ActionCell row={row} action={rowAction}>
              {column.cell(row)}
            </ActionCell>
          ) : (
            column.cell(row)
          )}
        </TableCell>
      ))}
    </TableRow>
  )
}

// The three non-row states are mutually exclusive and each occupies the body, so they are decided
// once here rather than by three independent conditionals in the table.
function BodyState<Row>({
  columns,
  isLoading,
  error,
  emptyTitle,
  emptyDescription,
}: {
  readonly columns: readonly DataTableColumn<Row>[]
  readonly isLoading: boolean
  readonly error: ReactNode
  readonly emptyTitle: string
  readonly emptyDescription: string | undefined
}) {
  if (isLoading) {
    return stateRow(columns, <LoadingState label={ADMIN_COPY.table.loading} rows={4} />)
  }

  if (error !== undefined) {
    return stateRow(columns, error)
  }

  return stateRow(
    columns,
    <EmptyState
      title={emptyTitle}
      {...(emptyDescription !== undefined ? { description: emptyDescription } : {})}
    />,
  )
}

const compareValues = (a: string | number, b: string | number): number =>
  typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b))

function useSortedRows<Row>(rows: readonly Row[], columns: readonly DataTableColumn<Row>[], sort: SortState | null) {
  return useMemo(() => {
    const sortValue = columns.find((column) => column.id === sort?.columnId)?.sortValue
    if (sort === null || sortValue === undefined) {
      return rows
    }
    const sign = sort.direction === 'ascending' ? 1 : -1
    return [...rows].sort((a, b) => sign * compareValues(sortValue(a), sortValue(b)))
  }, [rows, columns, sort])
}

function HeaderCell<Row>({ column, sort, onSort }: {
  readonly column: DataTableColumn<Row>
  readonly sort: SortState | null
  readonly onSort: (columnId: string) => void
}) {
  const direction = sort?.columnId === column.id ? sort.direction : undefined
  const Icon = direction === 'ascending' ? ArrowUpIcon : direction === 'descending' ? ArrowDownIcon : ArrowUpDownIcon

  return (
    <TableHead
      className={cn(column.className, 'align-middle')}
      {...(column.sortValue === undefined ? {} : { 'aria-sort': direction ?? 'none' })}
    >
      {column.sortValue === undefined ? column.header : (
        <button
          type="button"
          className="inline-flex cursor-pointer items-center gap-1 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          onClick={() => { onSort(column.id) }}
        >
          {column.header}
          <Icon aria-hidden="true" className={cn('size-3.5', direction === undefined && 'opacity-50')} />
        </button>
      )}
    </TableHead>
  )
}

// SPEC-020 §3.2: the one table every admin list is built from, so loading, empty and error states
// cannot drift between the review queue, the KB browser and usage analytics.
export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  rowAction,
  selectedKeys,
  isLoading = false,
  error,
  emptyTitle,
  emptyDescription,
  className,
  tableClassName,
}: DataTableProps<Row>) {
  const showRows = !isLoading && error === undefined && rows.length > 0
  const [sort, setSort] = useState<SortState | null>(null)
  const sortedRows = useSortedRows(rows, columns, sort)
  const toggleSort = (columnId: string) => {
    setSort((current) => ({
      columnId,
      direction: current?.columnId === columnId && current.direction === 'ascending' ? 'descending' : 'ascending',
    }))
  }

  return (
    <div className={cn('overflow-hidden rounded-xl border border-border bg-card', className)}>
    <Table className={tableClassName}>
      <TableCaption className="sr-only">{caption}</TableCaption>
      <TableHeader>
        <TableRow>
          {columns.map((column) => <HeaderCell key={column.id} column={column} sort={sort} onSort={toggleSort} />)}
        </TableRow>
      </TableHeader>
      <TableBody>
        {showRows ? (
          sortedRows.map((row) => <BodyRow key={rowKey(row)} row={row} columns={columns} rowKey={rowKey} rowAction={rowAction} selectedKeys={selectedKeys} />)
        ) : (
          <BodyState columns={columns} isLoading={isLoading} error={error} emptyTitle={emptyTitle} emptyDescription={emptyDescription} />
        )}
      </TableBody>
    </Table>
    </div>
  )
}
