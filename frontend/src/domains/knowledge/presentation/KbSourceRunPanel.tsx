import { AlertTriangleIcon } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import type { Problem } from '@/shared/http/exception'

import { processedCount, type KbSourceRun } from '@/domains/knowledge/domain/public'

const RUN = KNOWLEDGE_COPY.sources.run

export type KbSourceRunPanelProps = {
  readonly run: KbSourceRun
  readonly problem: Problem | null
}

function Metric({ label, value }: { readonly label: string; readonly value: number }) {
  return (
    <div className="grid gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  )
}

// The single global run, shared by both forms. Hidden while the server reports `idle`, so the
// page opens on the list and forms; live-announced because the poll updates it without any
// user action.
export function KbSourceRunPanel({ run, problem }: KbSourceRunPanelProps) {
  if (run.phase === 'idle' && problem === null) {
    return null
  }

  const typeLabel = run.sourceType === null ? null : RUN.type[run.sourceType]
  const heading = RUN.heading(RUN.phase[run.phase], run.sourceName, typeLabel)

  return (
    <Card aria-live="polite" aria-busy={run.phase === 'running' ? true : undefined} data-phase={run.phase}>
      <CardHeader>
        <CardTitle><h2>{RUN.title}</h2></CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="font-medium">{heading}</p>
        <dl className="flex flex-wrap gap-6">
          {run.sourceType === 'web' ? <Metric label={RUN.pagesCrawled} value={run.pagesCrawled} /> : null}
          <Metric label={RUN.processed} value={processedCount(run)} />
          <Metric label={RUN.chunksWritten} value={run.chunksWritten} />
        </dl>
        {run.error === null ? null : (
          <Alert variant="destructive">
            <AlertTriangleIcon aria-hidden />
            <AlertTitle>{RUN.phase.failed}</AlertTitle>
            <AlertDescription>{run.error}</AlertDescription>
          </Alert>
        )}
        {run.errors.length === 0 ? null : <p className="text-sm text-muted-foreground">{RUN.itemErrors(run.errors.length)}</p>}
        {problem === null ? null : <p role="alert" className="text-sm text-destructive">{RUN.statusError}</p>}
      </CardContent>
    </Card>
  )
}
