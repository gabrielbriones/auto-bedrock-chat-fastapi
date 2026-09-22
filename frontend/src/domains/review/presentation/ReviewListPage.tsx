import { useEffect, useRef } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Button } from '@/components/ui/button'
import { DebouncedFilterInput, FilterBar, FilterField } from '@/components/ui/composed/filter-bar'
import { ErrorState } from '@/components/ui/composed/error-state'
import { OffsetPagination } from '@/components/ui/composed/offset-pagination'
import { REVIEW_COPY } from '@/shared/copy/review'
import type { KbDocumentId } from '@/shared/kernel/branded'

import { toReviewQuery } from '@/domains/review/application/review-query'
import { pageWindow, selectionState, type FeedbackEntry, type FeedbackEntrySummary, type Rating, type ReviewDecision, type ReviewDecisionDraft, type ReviewStatus } from '@/domains/review/domain/public'
import { ReviewDrawer } from '@/domains/review/presentation/ReviewDrawer'
import { ReviewTable } from '@/domains/review/presentation/ReviewTable'

export type ReviewListSearch = {
  readonly rating?: 'all' | Rating | undefined
  readonly decision?: 'all' | ReviewDecision | undefined
  readonly tags?: string | undefined
  readonly from?: string | undefined
  readonly to?: string | undefined
  readonly offset: number
  readonly entry?: string | undefined
}

export type ReviewListPageProps = {
  readonly title: string
  readonly mode: 'queue' | 'reviewed'
  readonly search: ReviewListSearch
  readonly onSearchChange: (patch: ReviewListPatch) => void
}

export type ReviewListPatch = {
  readonly [Key in keyof ReviewListSearch]?: ReviewListSearch[Key] | undefined
}

const SelectFilter = ({ label, value, options, onChange }: {
  readonly label: string
  readonly value: string
  readonly options: readonly { readonly value: string; readonly label: string }[]
  readonly onChange: (value: string) => void
}) => (
  <FilterField label={label}>
    {(fieldId) => (
      <select id={fieldId} value={value} onChange={(event) => onChange(event.target.value)} className="h-8 rounded-lg border border-input bg-background px-2 text-sm">
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    )}
  </FilterField>
)

function ReviewFilters({ mode, search, onSearchChange }: Pick<ReviewListPageProps, 'mode' | 'search' | 'onSearchChange'>) {
  const reset = () => onSearchChange({
    ...(mode === 'queue' ? { rating: 'all' as const } : { decision: 'approved' as const }),
    tags: undefined,
    from: undefined,
    to: undefined,
    offset: 0,
  })
  return (
    <FilterBar onReset={reset}>
      {mode === 'queue' ? (
        <SelectFilter label={REVIEW_COPY.filters.rating} value={search.rating ?? 'all'} options={[
          { value: 'all', label: REVIEW_COPY.filters.all },
          { value: 'positive', label: REVIEW_COPY.filters.positive },
          { value: 'negative', label: REVIEW_COPY.filters.negative },
        ]} onChange={(rating) => onSearchChange({ rating: rating as ReviewListSearch['rating'], offset: 0 })} />
      ) : (
        <SelectFilter label={REVIEW_COPY.filters.decision} value={search.decision ?? 'approved'} options={[
          { value: 'approved', label: REVIEW_COPY.filters.approved },
          { value: 'rejected', label: REVIEW_COPY.filters.rejected },
          { value: 'all', label: REVIEW_COPY.filters.allWithPending },
        ]} onChange={(decision) => onSearchChange({ decision: decision as ReviewListSearch['decision'], offset: 0 })} />
      )}
      <FilterField label={REVIEW_COPY.filters.tags}>{(id) => <DebouncedFilterInput key={search.tags ?? ''} id={id} value={search.tags ?? ''} onCommit={(tags) => onSearchChange({ tags: tags || undefined, offset: 0 })} />}</FilterField>
      <FilterField label={REVIEW_COPY.filters.from}>{(id) => <DebouncedFilterInput key={search.from ?? ''} id={id} type="date" value={search.from ?? ''} onCommit={(from) => onSearchChange({ from: from || undefined, offset: 0 })} />}</FilterField>
      <FilterField label={REVIEW_COPY.filters.to}>{(id) => <DebouncedFilterInput key={search.to ?? ''} id={id} type="date" value={search.to ?? ''} onCommit={(to) => onSearchChange({ to: to || undefined, offset: 0 })} />}</FilterField>
    </FilterBar>
  )
}

const statusFor = (mode: ReviewListPageProps['mode'], decision: ReviewListSearch['decision']): ReviewStatus | null => {
  if (mode === 'queue') return 'pending_review'
  return decision === 'all' ? null : decision ?? 'approved'
}

const queryFor = (mode: ReviewListPageProps['mode'], search: ReviewListSearch) =>
  toReviewQuery({
    status: statusFor(mode, search.decision),
    rating: search.rating === 'all' || search.rating === undefined ? null : search.rating,
    ...(search.tags === undefined ? {} : { tags: search.tags }),
    ...(search.from === undefined ? {} : { from: search.from }),
    ...(search.to === undefined ? {} : { to: search.to }),
    offset: search.offset,
  })

function useReviewListLifecycle(mode: ReviewListPageProps['mode'], search: ReviewListSearch) {
  const { reviews } = useContainer()
  const snapshot = useContainerStore('reviews')
  const { rating, decision, tags, from, to, offset, entry } = search

  useEffect(() => {
    void reviews.load(queryFor(mode, { rating, decision, tags, from, to, offset }))
  }, [reviews, mode, rating, decision, tags, from, to, offset])

  useEffect(() => {
    if (entry === undefined) reviews.close()
    else void reviews.open(entry as FeedbackEntrySummary['id'])
  }, [reviews, entry])

  return { reviews, snapshot }
}

function BulkDeleteBar({ count, pending, onDelete }: {
  readonly count: number
  readonly pending: boolean
  readonly onDelete: () => void
}) {
  return count === 0 ? null : (
    <div className="flex items-center justify-between border-y border-border py-2">
      <span className="text-sm">{count}</span>
      <Button type="button" variant="destructive" disabled={pending} onClick={onDelete}>
        {REVIEW_COPY.bulk.delete(count)}
      </Button>
    </div>
  )
}

function useReviewListActions(
  reviews: ReturnType<typeof useReviewListLifecycle>['reviews'],
  search: ReviewListSearch,
  onSearchChange: ReviewListPageProps['onSearchChange'],
) {
  const routeEntryRef = useRef(search.entry)

  useEffect(() => {
    routeEntryRef.current = search.entry
  }, [search.entry])

  // The drawer is dismissible while a mutation is in flight, so a completion may land after the
  // reviewer has already opened another entry: only the originating entry may clear the URL.
  const closeIfStillRouted = (entryId: string | undefined) => {
    if (entryId !== undefined && routeEntryRef.current === entryId) onSearchChange({ entry: undefined })
  }

  return {
    open: (entry: FeedbackEntrySummary) => onSearchChange({ entry: entry.id }),
    save: async (entry: FeedbackEntry, draft: ReviewDecisionDraft) => {
      if (await reviews.saveDecision(entry.id, draft)) closeIfStillRouted(entry.id)
    },
    rollback: async (kbDocumentId: KbDocumentId) => {
      const originating = search.entry
      if (await reviews.rollback(kbDocumentId)) closeIfStillRouted(originating)
    },
    remove: async () => {
      const outcome = await reviews.removeSelected()
      if (outcome?.previousOffset !== null && outcome?.previousOffset !== undefined) onSearchChange({ offset: outcome.previousOffset })
    },
  }
}

export function ReviewListPage({ title, mode, search, onSearchChange }: ReviewListPageProps) {
  const { reviews, snapshot } = useReviewListLifecycle(mode, search)
  const { open, save, rollback, remove } = useReviewListActions(reviews, search, onSearchChange)

  const error = snapshot.listStatus === 'error' ? (
    <ErrorState description={REVIEW_COPY.queue.loadError} onRetry={() => { void reviews.load(queryFor(mode, search)) }} />
  ) : undefined

  return (
    <section className="grid gap-4 p-4">
      <h1 className="text-xl font-semibold">{title}</h1>
      <ReviewFilters mode={mode} search={search} onSearchChange={onSearchChange} />
      {mode === 'reviewed' ? <BulkDeleteBar count={snapshot.queue.selection.size} pending={snapshot.mutationPending} onDelete={() => { void remove() }} /> : null}
      <ReviewTable
        rows={snapshot.queue.entries}
        loading={snapshot.listStatus === 'loading'}
        {...(error === undefined ? {} : { error })}
        selected={snapshot.queue.selection}
        selectionState={selectionState(snapshot.queue)}
        selectable={mode === 'reviewed'}
        onOpen={open}
        onToggle={(entry) => reviews.toggleSelected(entry.id)}
        onToggleAll={() => reviews.toggleSelectAll()}
      />
      <OffsetPagination range={pageWindow(snapshot.queue)} onNavigate={(offset) => onSearchChange({ offset })} />
      <ReviewDrawer
        open={search.entry !== undefined}
        activeEntry={snapshot.activeEntry}
        detailStatus={snapshot.detailStatus}
        detailProblem={snapshot.detailProblem}
        mutationPending={snapshot.mutationPending}
        saveProblem={snapshot.saveProblem}
        synthesisPhase={snapshot.synthesisPhase}
        synthesisPending={snapshot.synthesisPending}
        synthesisProblem={snapshot.synthesisProblem}
        onClose={() => onSearchChange({ entry: undefined })}
        onSave={(entry, draft) => { void save(entry, draft) }}
        onSynthesize={(id) => { void reviews.synthesize(id) }}
        onRollback={(kbDocumentId) => { void rollback(kbDocumentId) }}
      />
    </section>
  )
}