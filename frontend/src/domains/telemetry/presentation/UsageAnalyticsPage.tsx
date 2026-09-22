import { useCallback, useEffect, type ReactNode } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { DataTable, type DataTableColumn } from '@/components/ui/composed/data-table'
import { EmptyState } from '@/components/ui/composed/empty-state'
import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { TELEMETRY_COPY } from '@/shared/copy/telemetry'

import type { TelemetrySnapshot } from '@/domains/telemetry/application/telemetry.store'
import {
  createCursorlessPage,
  type ModelUsageRow,
  type UserUsageRow,
} from '@/domains/telemetry/domain/public'
import { formatUsageNumber } from '@/domains/telemetry/presentation/format-usage'
import { ModelUsageChart } from '@/domains/telemetry/presentation/UsageCharts'
import { ByDaySection } from '@/domains/telemetry/presentation/ByDaySection'
import { ByUserSection } from '@/domains/telemetry/presentation/ByUserSection'
import type { UsageSearch, UsageSearchPatch } from '@/domains/telemetry/presentation/usage-search'

export type UsageAnalyticsPageProps = {
  readonly title: string
  readonly search: UsageSearch
  readonly onSearchChange: (patch: UsageSearchPatch) => void
}

const TOP_USER_LIMITS = [5, 10, 20, 50, 100] as const

const modelColumns: readonly DataTableColumn<ModelUsageRow>[] = [
  { id: 'model', header: TELEMETRY_COPY.summary.model, cell: (row) => row.modelId || '—' },
  { id: 'input', header: TELEMETRY_COPY.summary.input, cell: (row) => formatUsageNumber(row.tokens.input) },
  { id: 'output', header: TELEMETRY_COPY.summary.output, cell: (row) => formatUsageNumber(row.tokens.output) },
  { id: 'turns', header: TELEMETRY_COPY.summary.turns, cell: (row) => formatUsageNumber(row.turnCount) },
]

const topUserColumns: readonly DataTableColumn<UserUsageRow>[] = [
  { id: 'user', header: TELEMETRY_COPY.topUsers.user, cell: (row) => row.userId || '—' },
  { id: 'input', header: TELEMETRY_COPY.topUsers.input, cell: (row) => formatUsageNumber(row.tokens.input) },
  { id: 'output', header: TELEMETRY_COPY.topUsers.output, cell: (row) => formatUsageNumber(row.tokens.output) },
]

function SectionCard({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle><h2>{title}</h2></CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">{children}</CardContent>
    </Card>
  )
}

function SummarySection({ snapshot, load }: { readonly snapshot: TelemetrySnapshot['summary']; readonly load: () => void }) {
  const error = snapshot.status === 'error' ? <ErrorState description={TELEMETRY_COPY.summary.loadError} onRetry={load} /> : undefined
  return (
    <SectionCard title={TELEMETRY_COPY.summary.title}>
      {snapshot.status === 'loading' ? <LoadingState label={TELEMETRY_COPY.summary.title} /> : null}
      {snapshot.status === 'error' ? error : null}
      {snapshot.status === 'ready' && snapshot.rows.length === 0 ? <EmptyState title={TELEMETRY_COPY.summary.empty} /> : null}
      {snapshot.status === 'ready' && snapshot.rows.length > 0 ? (
        <>
          <ModelUsageChart rows={snapshot.rows} />
          <DataTable caption={TELEMETRY_COPY.summary.table} columns={modelColumns} rows={snapshot.rows} rowKey={(row) => row.modelId} emptyTitle={TELEMETRY_COPY.summary.empty} />
        </>
      ) : null}
    </SectionCard>
  )
}

function TopUsersSection({ snapshot, load, onLimitChange }: {
  readonly snapshot: TelemetrySnapshot['topUsers']
  readonly load: (limit: number) => void
  readonly onLimitChange: (limit: number) => void
}) {
  return (
    <SectionCard title={TELEMETRY_COPY.topUsers.title}>
      <div className="flex flex-wrap items-center gap-3">
        <Label htmlFor="usage-top-users-limit">{TELEMETRY_COPY.topUsers.limit}</Label>
        <Select value={String(snapshot.limit)} onValueChange={(value: string | null) => { if (value !== null) onLimitChange(Number(value)) }}>
          <SelectTrigger id="usage-top-users-limit" aria-label={TELEMETRY_COPY.topUsers.limit}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TOP_USER_LIMITS.map((limit) => <SelectItem key={limit} value={String(limit)}>{limit}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      {snapshot.status === 'loading' ? <LoadingState label={TELEMETRY_COPY.topUsers.title} /> : null}
      {snapshot.status === 'error' ? <ErrorState description={TELEMETRY_COPY.topUsers.loadError} onRetry={() => load(snapshot.limit)} /> : null}
      {snapshot.status === 'ready' ? <DataTable caption={TELEMETRY_COPY.topUsers.title} columns={topUserColumns} rows={snapshot.rows} rowKey={(row) => row.userId} emptyTitle={TELEMETRY_COPY.topUsers.empty} /> : null}
    </SectionCard>
  )
}

export function UsageAnalyticsPage({ title, search, onSearchChange }: UsageAnalyticsPageProps) {
  const { telemetry } = useContainer()
  const snapshot = useContainerStore('telemetry')
  const loadSummary = useCallback(() => { void telemetry.loadSummary() }, [telemetry])
  const loadTopUsers = useCallback((limit: number) => {
    void telemetry.loadTopUsers(limit)
  }, [telemetry])
  const loadByDay = useCallback((range: NonNullable<TelemetrySnapshot['byDay']['range']>) => {
    void telemetry.loadByDay(range)
  }, [telemetry])
  const loadByUser = useCallback((userId: string, offset: number) => {
    void telemetry.loadByUser({ userId, page: createCursorlessPage(offset, snapshot.byUser.page.limit, 0) })
  }, [snapshot.byUser.page.limit, telemetry])
  const resetByDay = useCallback(() => { telemetry.resetByDay() }, [telemetry])
  const resetByUser = useCallback(() => { telemetry.resetByUser() }, [telemetry])

  useEffect(() => { loadSummary() }, [loadSummary])
  useEffect(() => { loadTopUsers(search.topLimit ?? 10) }, [loadTopUsers, search.topLimit])

  return (
    <section className="grid gap-6 p-4">
      <h1 className="text-xl font-semibold">{title}</h1>
      <SummarySection snapshot={snapshot.summary} load={loadSummary} />
      <TopUsersSection snapshot={snapshot.topUsers} load={loadTopUsers} onLimitChange={(limit) => onSearchChange({ topLimit: limit })} />
      <ByDaySection key={`${search.from ?? ''}/${search.to ?? ''}`} search={search} snapshot={snapshot.byDay} load={loadByDay} reset={resetByDay} onSearchChange={onSearchChange} />
      <ByUserSection key={search.user ?? ''} search={search} snapshot={snapshot.byUser} load={loadByUser} reset={resetByUser} onSearchChange={onSearchChange} />
    </section>
  )
}