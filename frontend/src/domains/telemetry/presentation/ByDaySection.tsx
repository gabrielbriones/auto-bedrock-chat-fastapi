import { useEffect, useId, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { DataTable, type DataTableColumn } from '@/components/ui/composed/data-table'
import { EmptyState } from '@/components/ui/composed/empty-state'
import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { TELEMETRY_COPY } from '@/shared/copy/telemetry'
import { CalendarDate } from '@/shared/kernel/instant'
import { isErr, isOk } from '@/shared/kernel/result'

import type { TelemetrySnapshot } from '@/domains/telemetry/application/telemetry.store'
import { createDateRange, type DailyUsageRow } from '@/domains/telemetry/domain/public'
import { formatUsageNumber } from '@/domains/telemetry/presentation/format-usage'
import { DailyUsageChart } from '@/domains/telemetry/presentation/UsageCharts'
import type { UsageSearch, UsageSearchPatch } from '@/domains/telemetry/presentation/usage-search'

export type ByDaySectionProps = {
  readonly search: UsageSearch
  readonly snapshot: TelemetrySnapshot['byDay']
  readonly load: (range: NonNullable<TelemetrySnapshot['byDay']['range']>) => void
  readonly reset: () => void
  readonly onSearchChange: (patch: UsageSearchPatch) => void
}

const dailyColumns: readonly DataTableColumn<DailyUsageRow>[] = [
  { id: 'date', header: TELEMETRY_COPY.byDay.date, cell: (row) => row.date.toIso() },
  { id: 'input', header: TELEMETRY_COPY.byDay.input, cell: (row) => formatUsageNumber(row.tokens.input) },
  { id: 'output', header: TELEMETRY_COPY.byDay.output, cell: (row) => formatUsageNumber(row.tokens.output) },
  { id: 'turns', header: TELEMETRY_COPY.byDay.turns, cell: (row) => formatUsageNumber(row.turnCount) },
]

function SectionCard({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return <Card><CardHeader><CardTitle><h2>{title}</h2></CardTitle></CardHeader><CardContent className="grid gap-4">{children}</CardContent></Card>
}

function toCalendarDate(value: string): CalendarDate | null {
  const result = CalendarDate.fromIso(value)
  return isOk(result) ? result.value : null
}

function ByDayForm({
  start,
  end,
  onStartChange,
  onEndChange,
  onApply,
}: {
  readonly start: string
  readonly end: string
  readonly onStartChange: (value: string) => void
  readonly onEndChange: (value: string) => void
  readonly onApply: () => void
}) {
  const startId = useId()
  const endId = useId()

  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="grid gap-2"><Label htmlFor={startId}>{TELEMETRY_COPY.byDay.start}</Label><Input id={startId} type="date" value={start} onChange={(event) => onStartChange(event.target.value)} /></div>
      <div className="grid gap-2"><Label htmlFor={endId}>{TELEMETRY_COPY.byDay.end}</Label><Input id={endId} type="date" value={end} onChange={(event) => onEndChange(event.target.value)} /></div>
      <Button type="button" onClick={onApply}>{TELEMETRY_COPY.byDay.apply}</Button>
    </div>
  )
}

function ByDayResults({ snapshot, rangeReady, error, load }: {
  readonly snapshot: TelemetrySnapshot['byDay']
  readonly rangeReady: boolean
  readonly error: string | null
  readonly load: ByDaySectionProps['load']
}) {
  const invalidServerRange = snapshot.problem?.serverCode === 'invalid_date_range'
  if (error !== null) return <p role="alert" className="text-sm text-destructive">{error}</p>
  if (!rangeReady) return <EmptyState title={TELEMETRY_COPY.byDay.beforeApply} />
  if (snapshot.status === 'loading') return <LoadingState label={TELEMETRY_COPY.byDay.title} />
  if (snapshot.status === 'error') return <ErrorState description={TELEMETRY_COPY.byDay.loadError} onRetry={() => { if (snapshot.range !== null) load(snapshot.range) }} />
  if (snapshot.status === 'idle' && invalidServerRange) return <EmptyState title={TELEMETRY_COPY.byDay.beforeApply} />
  if (snapshot.status !== 'ready' || snapshot.rows.length === 0) return <EmptyState title={TELEMETRY_COPY.byDay.empty} />

  return <><DailyUsageChart rows={snapshot.rows} /><DataTable caption={TELEMETRY_COPY.byDay.table} columns={dailyColumns} rows={snapshot.rows} rowKey={(row) => row.date.toIso()} emptyTitle={TELEMETRY_COPY.byDay.empty} /></>
}

export function ByDaySection({ search, snapshot, load, reset, onSearchChange }: ByDaySectionProps) {
  const [startDraft, setStartDraft] = useState(search.from ?? '')
  const [endDraft, setEndDraft] = useState(search.to ?? '')
  const [validationError, setValidationError] = useState<string | null>(null)
  const rangeReady = search.from !== undefined && search.to !== undefined

  useEffect(() => {
    if (!rangeReady) return reset()
    const start = toCalendarDate(search.from)
    const end = toCalendarDate(search.to)
    const result = start === null || end === null ? null : createDateRange(start, end)
    if (result !== null && isOk(result)) load(result.value)
  }, [load, rangeReady, reset, search.from, search.to])

  const apply = () => {
    const start = toCalendarDate(startDraft)
    const end = toCalendarDate(endDraft)
    const result = start === null || end === null ? null : createDateRange(start, end)
    if (result === null || isErr(result)) {
      setValidationError(result === null ? TELEMETRY_COPY.byDay.beforeApply : result.error.message)
      reset()
      return
    }
    setValidationError(null)
    onSearchChange({ from: startDraft, to: endDraft })
  }

  const error = validationError ?? (snapshot.problem?.serverCode === 'invalid_date_range' ? TELEMETRY_COPY.byDay.invalidRange : null)
  return <SectionCard title={TELEMETRY_COPY.byDay.title}><ByDayForm start={startDraft} end={endDraft} onStartChange={setStartDraft} onEndChange={setEndDraft} onApply={apply} /><ByDayResults snapshot={snapshot} rangeReady={rangeReady} error={error} load={load} /></SectionCard>
}
