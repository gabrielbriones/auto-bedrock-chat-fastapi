import type { ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { AdminDrawer } from '@/components/ui/composed/admin-drawer'
import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { SanitizedMarkdown } from '@/shared/markdown/sanitized-markdown'
import { REVIEW_COPY } from '@/shared/copy/review'
import type { KbDocumentId } from '@/shared/kernel/branded'
import { cn } from '@/lib/utils'

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
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={entry.rating === 'negative' ? 'destructive' : 'secondary'}>
          {entry.rating === 'negative' ? REVIEW_COPY.filters.negative : REVIEW_COPY.filters.positive}
        </Badge>
        <ReviewStatusChip status={entry.reviewStatus} />
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted-foreground">{REVIEW_COPY.drawer.user}</dt><dd className="min-w-0 break-words">{entry.userId}</dd>
        <dt className="text-muted-foreground">{REVIEW_COPY.drawer.created}</dt><dd>{new Date(entry.createdAt.epochMilliseconds).toLocaleString()}</dd>
        <dt className="text-muted-foreground">{REVIEW_COPY.drawer.model}</dt><dd className="min-w-0 break-words">{entry.modelId}</dd>
        {sources.length === 0 ? null : <><dt className="text-muted-foreground">{REVIEW_COPY.drawer.sources}</dt><dd className="min-w-0 break-words">{sources.join(', ')}</dd></>}
      </dl>
    </Section>
  )
}

function Metadata({ entry }: { readonly entry: FeedbackEntry }) {
  const entries = metadataEntries(entry)
  return entries.length === 0 ? null : (
    <details className="border-t border-border">
      <summary className="cursor-pointer py-3 text-sm font-semibold">{REVIEW_COPY.drawer.metadata}</summary>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 pb-4 text-sm">
        {entries.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="capitalize">{humanize(key, value)}</dt>
            <dd className="min-w-0"><pre className="whitespace-pre-wrap break-words font-sans">{displayValue(value)}</pre></dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

function History({ entry }: { readonly entry: FeedbackEntry }) {
  const messages = entry.conversationHistory.length > 0
    ? entry.conversationHistory
    : entry.query.length > 0 ? [{ role: 'user', content: entry.query }] : []
  const hasResponse = entry.aiResponse.length > 0 && messages.at(-1)?.content !== entry.aiResponse
  const complete = [...messages, ...(hasResponse ? [{ role: 'response', content: entry.aiResponse }] : [])]
  return (
    <details open className="border-t border-border py-4">
      <summary className="cursor-pointer text-sm font-semibold">{REVIEW_COPY.drawer.history}</summary>
      <div className="mt-3 grid max-h-[min(60vh,36rem)] content-start gap-3 overflow-y-auto pr-1">
        {complete.map((message, index) => (
          <article
            key={`${message.role}-${index}`}
            className={cn(
              'min-w-0 max-w-prose rounded-lg px-4 py-2 break-words',
              message.role === 'user'
                ? 'ms-auto bg-message-user-bg text-message-user-fg'
                : 'bg-message-assistant-bg text-message-assistant-fg',
            )}
          >
            <p className="mb-1 text-xs font-semibold uppercase opacity-75">
              {message.role === 'response' ? REVIEW_COPY.drawer.response : message.role}
            </p>
            <SanitizedMarkdown content={message.content} />
          </article>
        ))}
      </div>
    </details>
  )
}

function PreviousDecision({ entry }: { readonly entry: FeedbackEntry }) {
  if (entry.review === null) return null
  return (
    <Section title={REVIEW_COPY.drawer.previousDecision}>
      {entry.review.tags.length === 0 ? null : (
        <div className="flex flex-wrap gap-1">{entry.review.tags.map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)}</div>
      )}
      {entry.review.comment === null ? null : <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{entry.review.comment}</p>}
    </Section>
  )
}

function EntryContent({ entry }: { readonly entry: FeedbackEntry }) {
  if (entry.correctionText === null && entry.userComment === null) {
    return null
  }
  return (
    <Section title={REVIEW_COPY.drawer.content}>
      {entry.correctionText === null ? null : <div className="grid gap-1"><h4 className="text-xs font-medium text-muted-foreground">{REVIEW_COPY.drawer.correction}</h4><p className="whitespace-pre-wrap break-words text-sm">{entry.correctionText}</p></div>}
      {entry.userComment === null ? null : <div className="grid gap-1"><h4 className="text-xs font-medium text-muted-foreground">{REVIEW_COPY.drawer.comment}</h4><p className="whitespace-pre-wrap break-words text-sm">{entry.userComment}</p></div>}
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
      <EntryContent entry={entry} />
      <PreviousDecision entry={entry} />
      <ReviewForm
        key={entry.id}
        entry={entry}
        pending={busy}
        problem={saveProblem}
        onSave={(draft) => onSave(entry, draft)}
      />
      <Metadata entry={entry} />
      <History entry={entry} />
      <SynthesisSection
        entry={entry}
        phase={synthesisPhase}
        pending={busy}
        problem={synthesisProblem}
        onSynthesize={onSynthesize}
        onRollback={onRollback}
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