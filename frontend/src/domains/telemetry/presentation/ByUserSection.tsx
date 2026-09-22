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

import type { TelemetrySnapshot } from '@/domains/telemetry/application/telemetry.store'
import type { SessionUsageRow } from '@/domains/telemetry/domain/public'
import { CursorlessPagination } from '@/domains/telemetry/presentation/CursorlessPagination'
import { formatUsageInstant, formatUsageNumber, truncateSessionId } from '@/domains/telemetry/presentation/format-usage'
import type { UsageSearch, UsageSearchPatch } from '@/domains/telemetry/presentation/usage-search'

export type ByUserSectionProps = {
  readonly search: UsageSearch
  readonly snapshot: TelemetrySnapshot['byUser']
  readonly load: (userId: string, offset: number) => void
  readonly reset: () => void
  readonly onSearchChange: (patch: UsageSearchPatch) => void
}

const sessionColumns: readonly DataTableColumn<SessionUsageRow>[] = [
  { id: 'session', header: TELEMETRY_COPY.byUser.session, cell: (row) => truncateSessionId(row.sessionId) },
  { id: 'model', header: TELEMETRY_COPY.byUser.model, cell: (row) => row.modelId || '—' },
  { id: 'input', header: TELEMETRY_COPY.byUser.input, cell: (row) => formatUsageNumber(row.tokens.input) },
  { id: 'output', header: TELEMETRY_COPY.byUser.output, cell: (row) => formatUsageNumber(row.tokens.output) },
  { id: 'timestamp', header: TELEMETRY_COPY.byUser.timestamp, cell: (row) => formatUsageInstant(row.turnAt.epochMilliseconds) },
]

function SectionCard({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return <Card><CardHeader><CardTitle><h2>{title}</h2></CardTitle></CardHeader><CardContent className="grid gap-4">{children}</CardContent></Card>
}

export function ByUserSection({ search, snapshot, load, reset, onSearchChange }: ByUserSectionProps) {
  const inputId = useId()
  const [userDraft, setUserDraft] = useState(search.user ?? '')
  const appliedUserId = search.user

  useEffect(() => {
    if (appliedUserId === undefined) return reset()
    load(appliedUserId, search.offset)
  }, [appliedUserId, load, reset, search.offset])

  const apply = () => {
    const user = userDraft.trim()
    onSearchChange(user.length === 0 ? { user: undefined, offset: 0 } : { user, offset: 0 })
  }

  return (
    <SectionCard title={TELEMETRY_COPY.byUser.title}>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => { event.preventDefault(); apply() }}>
        <div className="grid min-w-64 flex-1 gap-2"><Label htmlFor={inputId}>{TELEMETRY_COPY.byUser.user}</Label><Input id={inputId} type="search" placeholder={TELEMETRY_COPY.byUser.userPlaceholder} value={userDraft} onChange={(event) => setUserDraft(event.target.value)} /></div>
        <Button type="submit">{TELEMETRY_COPY.byUser.apply}</Button>
      </form>
      {appliedUserId === undefined ? <EmptyState title={TELEMETRY_COPY.byUser.beforeApply} /> : null}
      {appliedUserId !== undefined && snapshot.status === 'loading' ? <LoadingState label={TELEMETRY_COPY.byUser.title} /> : null}
      {appliedUserId !== undefined && snapshot.status === 'error' ? <ErrorState description={TELEMETRY_COPY.byUser.loadError} onRetry={() => load(appliedUserId, search.offset)} /> : null}
      {appliedUserId !== undefined && snapshot.status === 'ready' ? <DataTable caption={TELEMETRY_COPY.byUser.title} columns={sessionColumns} rows={snapshot.rows} rowKey={(row) => `${row.sessionId}-${row.turnAt.epochMilliseconds}`} emptyTitle={TELEMETRY_COPY.byUser.empty} /> : null}
      {appliedUserId !== undefined && snapshot.status === 'ready' ? <CursorlessPagination page={snapshot.page} onNavigate={(offset) => onSearchChange({ offset })} /> : null}
    </SectionCard>
  )
}
