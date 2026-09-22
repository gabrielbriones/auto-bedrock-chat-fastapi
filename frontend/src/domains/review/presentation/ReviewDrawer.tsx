import { useEffect, useRef, type ReactNode } from 'react'

import { AdminDrawer } from '@/components/ui/composed/admin-drawer'
import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { SanitizedMarkdown } from '@/shared/markdown/sanitized-markdown'
import { REVIEW_COPY } from '@/shared/copy/review'
import type { KbDocumentId } from '@/shared/kernel/branded'

import type { FeedbackEntry, ReviewDecisionDraft } from '@/domains/review/domain/public'
import type { ReviewSnapshot } from '@/domains/review/application/review.store'
import { ReviewForm } from '@/domains/review/presentation/ReviewForm'
import { ReviewStatusChip } from '@/domains/review/presentation/ReviewStatusChip'
import { SynthesisSection } from '@/domains/review/presentation/SynthesisSection'

export type ReviewDrawerProps = Pick<
  ReviewSnapshot,
  | 'activeEntry'
  | 'detailStatus'
  | 'detailProblem'
  | 'mutationPending'
  | 'saveProblem'
  | 'synthesisPhase'
  | 'synthesisPending'
  | 'synthesisProblem'
> & {
  readonly open: boolean
  readonly onClose: () => void
  readonly onSave: (entry: FeedbackEntry, draft: ReviewDecisionDraft) => void
  readonly onSynthesize: (id: FeedbackEntry['id']) => void
  readonly onRollback: (kbDocumentId: KbDocumentId) => void
}

export const Section = ({ title, children }: { readonly title: string; readonly children: ReactNode }) => (
  <section className="grid gap-2 border-t border-border py-4 first:border-t-0 first:pt-0">
    <h3 className="text-sm font-semibold">{title}</h3>
    {children}
  </section>
)

const metadataEntries = (entry: FeedbackEntry) =>
  Object.entries(entry.entryMetadata).filter(([key]) => key.toLowerCase() !== 'user_id')

const humanize = (key: string, value: unknown): string => {
  const withoutBooleanPrefix = typeof value === 'boolean' ? key.replace(/^is_/, '') : key
  return withoutBooleanPrefix.replaceAll('_', ' ')
}

const displayValue = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '—'
  }
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}

function EntryDetails({ entry }: { readonly entry: FeedbackEntry }) {
  const sources = entry.kbSourcesUsed.map((source) => source.title ?? source.source).filter(Boolean)
  return (
    <Section title={REVIEW_COPY.drawer.details}>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
        <dt>{REVIEW_COPY.drawer.user}</dt><dd className="truncate">{entry.userId}</dd>
        <dt>{REVIEW_COPY.table.rating}</dt><dd>{entry.rating}</dd>
        <dt>{REVIEW_COPY.table.status}</dt><dd><ReviewStatusChip status={entry.reviewStatus} /></dd>
        <dt>{REVIEW_COPY.drawer.created}</dt><dd>{new Date(entry.createdAt.epochMilliseconds).toLocaleString()}</dd>
        <dt>{REVIEW_COPY.drawer.model}</dt><dd className="truncate">{entry.modelId}</dd>
        {sources.length === 0 ? null : <><dt>{REVIEW_COPY.drawer.sources}</dt><dd>{sources.join(', ')}</dd></>}
      </dl>
    </Section>
  )
}

function Metadata({ entry }: { readonly entry: FeedbackEntry }) {
  const entries = metadataEntries(entry)
  return entries.length === 0 ? null : (
    <details>
      <summary className="cursor-pointer py-3 text-sm font-semibold">{REVIEW_COPY.drawer.metadata}</summary>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 pb-4 text-sm">
        {entries.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="capitalize">{humanize(key, value)}</dt>
            <dd><pre className="whitespace-pre-wrap font-sans">{displayValue(value)}</pre></dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

function History({ entry }: { readonly entry: FeedbackEntry }) {
  const endRef = useRef<HTMLDivElement>(null)
  const messages = entry.conversationHistory.length > 0
    ? entry.conversationHistory
    : entry.query.length > 0 ? [{ role: 'user', content: entry.query }] : []
  const complete = [...messages, ...(entry.aiResponse.length > 0 ? [{ role: 'assistant', content: entry.aiResponse }] : [])]
  useEffect(() => {
    const container = endRef.current?.parentElement
    if (container) {
      container.scrollTop = container.scrollHeight
    }
  }, [entry.id, entry.conversationHistory.length, entry.aiResponse])
  return (
    <Section title={REVIEW_COPY.drawer.history}>
      <div className="grid h-[clamp(18rem,45vh,36rem)] content-start gap-3 overflow-y-auto rounded-md bg-muted/30 p-3">
        {complete.map((message, index) => (
          <article key={`${message.role}-${index}`} className="rounded-md border border-border bg-background p-3">
            <p className="mb-2 text-xs font-semibold uppercase text-muted-foreground">{message.role}</p>
            <SanitizedMarkdown content={message.content} />
          </article>
        ))}
        <div ref={endRef} />
      </div>
    </Section>
  )
}

function PreviousDecision({ entry }: { readonly entry: FeedbackEntry }) {
  if (entry.review === null) return null
  return (
    <Section title={REVIEW_COPY.drawer.previousDecision}>
      <p className="text-sm">{entry.review.tags.join(', ')}</p>
      {entry.review.comment === null ? null : <p className="text-sm">{entry.review.comment}</p>}
    </Section>
  )
}

function EntryContent({ entry }: { readonly entry: FeedbackEntry }) {
  if (entry.correctionText === null && entry.userComment === null) {
    return null
  }
  return (
    <Section title={REVIEW_COPY.drawer.content}>
      {entry.correctionText === null ? null : <pre className="whitespace-pre-wrap text-sm">{entry.correctionText}</pre>}
      {entry.userComment === null ? null : <p className="text-sm">{entry.userComment}</p>}
    </Section>
  )
}

function DrawerEntry({
  entry,
  mutationPending,
  saveProblem,
  synthesisPhase,
  synthesisPending,
  synthesisProblem,
  onSave,
  onSynthesize,
  onRollback,
}: Pick<
  ReviewDrawerProps,
  'mutationPending' | 'saveProblem' | 'synthesisPhase' | 'synthesisPending' | 'synthesisProblem' | 'onSave' | 'onSynthesize' | 'onRollback'
> & { readonly entry: FeedbackEntry }) {
  // One shared lock: the store refuses a save while a synthesis mutation runs, and vice versa.
  const busy = mutationPending || synthesisPending
  return (
    <>
      <EntryDetails entry={entry} />
      <Metadata entry={entry} />
      <History entry={entry} />
      <EntryContent entry={entry} />
      <PreviousDecision entry={entry} />
      <SynthesisSection
        entry={entry}
        phase={synthesisPhase}
        pending={busy}
        problem={synthesisProblem}
        onSynthesize={onSynthesize}
        onRollback={onRollback}
      />
      <ReviewForm
        key={entry.id}
        entry={entry}
        pending={busy}
        problem={saveProblem}
        onSave={(draft) => onSave(entry, draft)}
      />
    </>
  )
}

export function ReviewDrawer(props: ReviewDrawerProps) {
  let content: ReactNode = null
  if (props.detailStatus === 'loading') {
    content = <LoadingState label={REVIEW_COPY.drawer.loading} />
  } else if (props.detailStatus === 'error') {
    content = <ErrorState description={REVIEW_COPY.drawer.loadError} />
  } else if (props.activeEntry !== null) {
    content = (
      <DrawerEntry
        entry={props.activeEntry}
        mutationPending={props.mutationPending}
        saveProblem={props.saveProblem}
        synthesisPhase={props.synthesisPhase}
        synthesisPending={props.synthesisPending}
        synthesisProblem={props.synthesisProblem}
        onSave={props.onSave}
        onSynthesize={props.onSynthesize}
        onRollback={props.onRollback}
      />
    )
  }

  return (
    <AdminDrawer
      open={props.open}
      onOpenChange={(open) => { if (!open) props.onClose() }}
      title={REVIEW_COPY.drawer.title}
      className="data-[side=right]:w-full data-[side=right]:sm:max-w-none data-[side=right]:md:w-3/5 data-[side=right]:md:max-w-6xl"
    >
      {content}
    </AdminDrawer>
  )
}