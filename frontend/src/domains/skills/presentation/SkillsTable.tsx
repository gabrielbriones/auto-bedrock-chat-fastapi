import type { MouseEvent, ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { DataTable, type DataTableColumn } from '@/components/ui/composed/data-table'
import { Switch } from '@/components/ui/switch'
import { SKILLS_COPY } from '@/shared/copy/skills'

import type { SkillSummary } from '@/domains/skills/domain/public'
import { formatInstant } from '@/domains/skills/presentation/format'

const LIST = SKILLS_COPY.list

// The row itself opens the editor; its own controls must not.
const stopRow = (event: MouseEvent) => {
  event.stopPropagation()
}

export type SkillsTableProps = {
  readonly rows: readonly SkillSummary[]
  readonly loading: boolean
  readonly error?: ReactNode
  readonly emptyTitle: string
  readonly toggling: readonly string[]
  readonly deleting: string | null
  readonly onOpen: (row: SkillSummary) => void
  readonly onToggle: (row: SkillSummary, enabled: boolean) => void
  readonly onDelete: (row: SkillSummary) => void
}

const DETAIL_COLUMNS: readonly DataTableColumn<SkillSummary>[] = [
  { id: 'name', header: LIST.name, cell: (row) => row.name, className: 'font-mono', sortValue: (row) => row.name },
  { id: 'displayName', header: LIST.displayName, cell: (row) => row.displayName, className: 'whitespace-normal font-medium', sortValue: (row) => row.displayName },
  { id: 'description', header: LIST.description, cell: (row) => row.description, className: 'min-w-48 whitespace-normal text-muted-foreground' },
]

const HISTORY_COLUMNS: readonly DataTableColumn<SkillSummary>[] = [
  { id: 'revision', header: LIST.revision, cell: (row) => row.revision, className: 'tabular-nums', sortValue: (row) => row.revision },
  { id: 'updated', header: LIST.updated, cell: (row) => formatInstant(row.updatedAt), sortValue: (row) => row.updatedAt?.epochMilliseconds ?? 0 },
]

export function SkillsTable({ rows, loading, error, emptyTitle, toggling, deleting, onOpen, onToggle, onDelete }: SkillsTableProps) {
  const columns: readonly DataTableColumn<SkillSummary>[] = [
    ...DETAIL_COLUMNS,
    {
      id: 'enabled',
      header: LIST.enabled,
      cell: (row) => (
        <Switch
          aria-label={LIST.toggleLabel(row.name)}
          checked={row.enabled}
          disabled={toggling.includes(row.name)}
          onClick={stopRow}
          onCheckedChange={(checked) => onToggle(row, checked)}
        />
      ),
    },
    ...HISTORY_COLUMNS,
    {
      id: 'actions',
      header: <span className="sr-only">{LIST.actions}</span>,
      cell: (row) => (
        <Button
          type="button"
          variant="destructive"
          size="sm"
          aria-label={LIST.deleteLabel(row.name)}
          disabled={deleting !== null}
          onClick={(event) => { stopRow(event); onDelete(row) }}
        >
          {deleting === row.name ? LIST.deleting : LIST.delete}
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
      rowKey={(row) => row.name}
      rowAction={{ label: (row) => LIST.open(row.name), onActivate: onOpen }}
      isLoading={loading}
      {...(error === undefined ? {} : { error })}
      emptyTitle={emptyTitle}
    />
  )
}
