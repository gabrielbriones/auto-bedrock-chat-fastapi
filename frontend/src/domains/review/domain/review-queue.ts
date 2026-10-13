import type { FeedbackEntryId } from '@/shared/kernel/branded'
import type { CalendarDate } from '@/shared/kernel/instant'
import { DEFAULT_PAGE_LIMIT, offsetWindow, type OffsetPage, type OffsetWindow } from '@/shared/kernel/pagination'

import {
  isDeletable,
  type FeedbackEntrySummary,
  type Rating,
  type ReviewStatus,
} from '@/domains/review/domain/feedback-entry'

export type ReviewFilters = {
  readonly status: ReviewStatus | null
  readonly rating: Rating | null
  readonly tags: readonly string[]
  readonly dateFrom: CalendarDate | null
  readonly dateTo: CalendarDate | null
}

export type SelectionState = 'none' | 'partial' | 'all'

// DESIGN-001 §6.1. Filters, page and selection are one aggregate because the rules that matter
// hold *between* them: changing a filter resets the page, and a page that changes drops the
// selection. Splitting them is how the legacy dashboard lost track of both.
export type ReviewQueue = {
  readonly filters: ReviewFilters
  readonly page: OffsetPage
  readonly total: number
  readonly entries: readonly FeedbackEntrySummary[]
  readonly selection: ReadonlySet<FeedbackEntryId>
}

export const noFilters: ReviewFilters = {
  status: null,
  rating: null,
  tags: [],
  dateFrom: null,
  dateTo: null,
}

export const emptyReviewQueue: ReviewQueue = {
  filters: noFilters,
  page: { limit: DEFAULT_PAGE_LIMIT, offset: 0 },
  total: 0,
  entries: [],
  selection: new Set(),
}

export const createReviewQueue = (
  filters: ReviewFilters,
  page: OffsetPage = { limit: DEFAULT_PAGE_LIMIT, offset: 0 },
): ReviewQueue => ({ ...emptyReviewQueue, filters, page })

// FR-REV-002: any filter change returns to the first page. Selection goes with it — it belongs to
// the rows that were on screen, which are about to be replaced.
export const withFilters = (queue: ReviewQueue, filters: ReviewFilters): ReviewQueue => ({
  ...queue,
  filters,
  page: { ...queue.page, offset: 0 },
  entries: [],
  total: 0,
  selection: new Set(),
})

export const withOffset = (queue: ReviewQueue, offset: number): ReviewQueue => ({
  ...queue,
  page: { ...queue.page, offset: offset > 0 ? offset : 0 },
  entries: [],
  selection: new Set(),
})

// FR-REV-011c: a refetch is a new page as far as selection is concerned. This is also what makes
// the bulk delete of FIX-16 safe — nothing survives to point at a row the server no longer has.
export const withPage = (
  queue: ReviewQueue,
  result: { readonly entries: readonly FeedbackEntrySummary[]; readonly total: number; readonly offset: number },
): ReviewQueue => ({
  ...queue,
  entries: result.entries,
  total: result.total,
  page: { ...queue.page, offset: result.offset },
  selection: new Set(),
})

export const pageWindow = (queue: ReviewQueue): OffsetWindow => offsetWindow(queue.page, queue.total)

/** FR-REV-011b: a page emptied by a bulk delete steps back rather than stranding the reviewer. */
export const shouldStepBack = (queue: ReviewQueue): boolean =>
  queue.entries.length === 0 && queue.page.offset > 0

const selectableIds = (queue: ReviewQueue): readonly FeedbackEntryId[] =>
  queue.entries.filter(isDeletable).map((entry) => entry.id)

export const isSelectable = (queue: ReviewQueue, id: FeedbackEntryId): boolean =>
  queue.entries.some((entry) => entry.id === id && isDeletable(entry))

// The aggregate invariant: only rejected entries may be selected. Enforced on the way in, so no
// caller can build a selection the delete endpoint would answer 409 for.
export const toggleSelection = (queue: ReviewQueue, id: FeedbackEntryId): ReviewQueue => {
  if (!isSelectable(queue, id)) {
    return queue
  }

  const selection = new Set(queue.selection)
  if (!selection.delete(id)) {
    selection.add(id)
  }

  return { ...queue, selection }
}

export const selectAllSelectable = (queue: ReviewQueue): ReviewQueue => ({
  ...queue,
  selection: new Set(selectableIds(queue)),
})

export const clearSelection = (queue: ReviewQueue): ReviewQueue => ({ ...queue, selection: new Set() })

// FR-REV-011: drives the header control's indeterminate state. A page with no rejected rows is
// `none`, so the control never claims "all selected" with nothing to delete.
export const selectionState = (queue: ReviewQueue): SelectionState => {
  if (queue.selection.size === 0) {
    return 'none'
  }

  return queue.selection.size === selectableIds(queue).length ? 'all' : 'partial'
}
