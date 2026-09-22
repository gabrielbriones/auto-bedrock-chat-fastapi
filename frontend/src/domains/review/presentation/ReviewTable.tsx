import type { ReactNode } from 'react'

import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { DataTable, type DataTableColumn } from '@/components/ui/composed/data-table'
import { REVIEW_COPY } from '@/shared/copy/review'

import type { FeedbackEntrySummary, SelectionState } from '@/domains/review/domain/public'
import { isDeletable } from '@/domains/review/domain/public'
import { ReviewStatusChip } from '@/domains/review/presentation/ReviewStatusChip'

const visibleTags = (tags: readonly string[]): ReactNode => (
  <div className="flex max-w-64 flex-wrap gap-1">
    {tags.slice(0, 5).map((tag) => (
      <Badge key={tag} variant="outline">{tag}</Badge>
    ))}
    {tags.length > 5 ? <Badge variant="secondary">{REVIEW_COPY.table.moreTags(tags.length - 5)}</Badge> : null}
  </div>
)

const baseColumns: readonly DataTableColumn<FeedbackEntrySummary>[] = [
  { id: 'user', header: REVIEW_COPY.table.user, cell: (entry) => entry.userId, className: 'align-top' },
  { id: 'rating', header: REVIEW_COPY.table.rating, cell: (entry) => entry.rating, className: 'align-top' },
  { id: 'status', header: REVIEW_COPY.table.status, cell: (entry) => <ReviewStatusChip status={entry.reviewStatus} />, className: 'align-top' },
  {
    id: 'query',
    header: REVIEW_COPY.table.query,
    cell: (entry) => (
      <span className="line-clamp-3 whitespace-pre-wrap [overflow-wrap:anywhere]" title={entry.query}>
        {entry.query}
      </span>
    ),
    className: 'w-[32rem] max-w-[50vw] whitespace-normal align-top',
  },
  { id: 'created', header: REVIEW_COPY.table.created, cell: (entry) => new Date(entry.createdAt.epochMilliseconds).toLocaleString(), className: 'align-top' },
  { id: 'tags', header: REVIEW_COPY.table.tags, cell: (entry) => visibleTags(entry.reviewerTags), className: 'whitespace-normal align-top' },
]

export type ReviewTableProps = {
  readonly rows: readonly FeedbackEntrySummary[]
  readonly loading: boolean
  readonly error?: ReactNode
  readonly selected: ReadonlySet<string>
  readonly selectionState: SelectionState
  readonly selectable: boolean
  readonly onOpen: (entry: FeedbackEntrySummary) => void
  readonly onToggle: (entry: FeedbackEntrySummary) => void
  readonly onToggleAll: () => void
}

const selectionColumn = (
  state: SelectionState,
  selected: ReadonlySet<string>,
  onToggle: (entry: FeedbackEntrySummary) => void,
  onToggleAll: () => void,
): DataTableColumn<FeedbackEntrySummary> => ({
  id: 'selection',
  header: (
    <Checkbox
      aria-label={REVIEW_COPY.table.selectAll}
      checked={state === 'all'}
      indeterminate={state === 'partial'}
      onCheckedChange={onToggleAll}
    />
  ),
  cell: (entry) =>
    isDeletable(entry) ? (
      <Checkbox
        aria-label={REVIEW_COPY.table.selectEntry(entry.query)}
        checked={selected.has(entry.id)}
        onCheckedChange={() => onToggle(entry)}
        onClick={(event) => event.stopPropagation()}
      />
    ) : null,
})

export function ReviewTable(props: ReviewTableProps) {
  const columns = props.selectable
    ? [...baseColumns, selectionColumn(props.selectionState, props.selected, props.onToggle, props.onToggleAll)]
    : baseColumns

  return (
    <DataTable
      caption="Feedback review entries"
      columns={columns}
      rows={props.rows}
      rowKey={(entry) => entry.id}
      rowAction={{ label: (entry) => entry.query, onActivate: props.onOpen }}
      selectedKeys={props.selected}
      isLoading={props.loading}
      {...(props.error === undefined ? {} : { error: props.error })}
      emptyTitle={REVIEW_COPY.queue.empty}
    />
  )
}