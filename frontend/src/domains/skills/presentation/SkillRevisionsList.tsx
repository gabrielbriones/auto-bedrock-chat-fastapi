import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { SKILLS_COPY } from '@/shared/copy/skills'

import type { LoadStatus } from '@/domains/skills/application/skills.store'
import type { SkillRevisionSummary } from '@/domains/skills/domain/public'
import { formatInstant } from '@/domains/skills/presentation/format'

const COPY = SKILLS_COPY.revisions

export type SkillRevisionsListProps = {
  readonly revisions: readonly SkillRevisionSummary[]
  readonly status: LoadStatus
  readonly currentRevision: number
  readonly disabled: boolean
  readonly onRollback: (revision: number) => void
}

function RevisionsBody({ revisions, status, currentRevision, disabled, onRollback }: SkillRevisionsListProps) {
  if (status === 'loading' && revisions.length === 0) return <LoadingState rows={2} />
  if (status === 'error') return <ErrorState description={COPY.loadError} />
  if (revisions.length === 0) return <p className="text-sm text-muted-foreground">{COPY.empty}</p>

  return (
    <ol className="grid gap-2">
      {revisions.map((entry) => (
        <li key={entry.revision} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm">
          <div className="grid gap-0.5">
            <span className="font-medium">
              {COPY.revision(entry.revision)}
              {entry.revision === currentRevision ? <Badge variant="secondary" className="ml-2">{COPY.current}</Badge> : null}
            </span>
            <span className="text-xs text-muted-foreground">
              {formatInstant(entry.updatedAt)}
              {entry.updatedBy === null ? null : ` ${COPY.by(entry.updatedBy)}`}
            </span>
          </div>
          {entry.revision === currentRevision ? null : (
            <Button type="button" variant="outline" size="sm" aria-label={COPY.rollbackLabel(entry.revision)} disabled={disabled} onClick={() => onRollback(entry.revision)}>
              {COPY.rollback}
            </Button>
          )}
        </li>
      ))}
    </ol>
  )
}

export function SkillRevisionsList(props: SkillRevisionsListProps) {
  return (
    <section className="grid gap-2" aria-labelledby="skill-revisions-title">
      <h3 id="skill-revisions-title" className="text-sm font-semibold">{COPY.title}</h3>
      <RevisionsBody {...props} />
    </section>
  )
}
