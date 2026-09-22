import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { DataTable, type DataTableColumn } from '@/components/ui/composed/data-table'
import { REVIEW_COPY } from '@/shared/copy/review'

import type { FeedbackStats } from '@/domains/review/application/ports'

export type TopTagsChartProps = {
  readonly tags: FeedbackStats['topTags']
}

const MAX_BARS = 10

type TagRow = FeedbackStats['topTags'][number]

const columns: readonly DataTableColumn<TagRow>[] = [
  { id: 'tag', header: REVIEW_COPY.stats.tagColumn, cell: (row) => row.tag },
  { id: 'count', header: REVIEW_COPY.stats.countColumn, cell: (row) => row.count.toLocaleString() },
]

// FR-REV-018: at most 10 bars, scaled against the max count across the *full* array (not just the
// bars shown) so a chart truncated to 10 never silently rescales itself relative to its own slice.
// The chart is decorative — a screen reader gets the same data from the `DataTable` beside it.
export function TopTagsChart({ tags }: TopTagsChartProps) {
  const top = tags.slice(0, MAX_BARS)
  const max = tags.reduce((highest, tag) => Math.max(highest, tag.count), 1)

  return (
    <section className="grid gap-4">
      <h2 className="text-sm font-semibold">{REVIEW_COPY.stats.topTagsChart}</h2>
      {top.length === 0 ? (
        <p className="text-sm text-muted-foreground">{REVIEW_COPY.stats.topTagsEmpty}</p>
      ) : (
        <div aria-hidden="true" inert className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={[...top]} layout="vertical" margin={{ left: 24, right: 24 }}>
              <CartesianGrid horizontal={false} strokeOpacity={0.2} />
              <XAxis type="number" domain={[0, max]} allowDecimals={false} />
              <YAxis type="category" dataKey="tag" width={120} />
              <Tooltip />
              <Bar dataKey="count" radius={4} className="fill-primary" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <DataTable
        caption={REVIEW_COPY.stats.topTagsTable}
        columns={columns}
        rows={top}
        rowKey={(row) => row.tag}
        emptyTitle={REVIEW_COPY.stats.topTagsEmpty}
      />
    </section>
  )
}
