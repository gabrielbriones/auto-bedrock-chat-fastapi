import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { DataTable, type DataTableColumn } from '@/components/ui/composed/data-table'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'

import type { KbSourceSummary } from '@/domains/knowledge/domain/public'

const LIST = KNOWLEDGE_COPY.sources.list

export type KbSourcesTableProps = {
  readonly rows: readonly KbSourceSummary[]
  readonly loading: boolean
  readonly error?: ReactNode
  /** The source whose delete is in flight; every delete button is disabled while one is. */
  readonly deleting: string | null
  readonly onDelete: (row: KbSourceSummary) => void
}

// Rows are identified by the `source` name rather than a run id — the API keeps no run history,
// only the documents each source left behind. Re-running a source goes through the forms below
// (same name → confirm-then-override), so the only row action is Delete.
export function KbSourcesTable({ rows, loading, error, deleting, onDelete }: KbSourcesTableProps) {
  const columns: readonly DataTableColumn<KbSourceSummary>[] = [
    { id: 'source', header: LIST.source, cell: (row) => row.source, className: 'whitespace-normal font-medium' },
    { id: 'count', header: LIST.documents, cell: (row) => row.count, className: 'tabular-nums' },
    {
      id: 'actions',
      header: <span className="sr-only">{LIST.actions}</span>,
      cell: (row) => (
        <Button
          type="button"
          variant="destructive"
          size="sm"
          aria-label={LIST.deleteLabel(row.source)}
          disabled={deleting !== null}
          onClick={() => onDelete(row)}
        >
          {deleting === row.source ? LIST.deleting : LIST.delete}
        </Button>
      ),
      className: 'w-0 text-right',
    },
  ]

  return (
    <DataTable
      caption={LIST.caption}
      columns={columns}
      rows={rows}
      rowKey={(row) => row.source}
      isLoading={loading}
      {...(error === undefined ? {} : { error })}
      emptyTitle={LIST.empty}
    />
  )
}
