import { describe, expect, it } from '@jest/globals'

import { feedbackEntryId } from '@/shared/kernel/branded'
import {
  clearSelection,
  createReviewQueue,
  emptyReviewQueue,
  isSelectable,
  noFilters,
  pageWindow,
  selectAllSelectable,
  selectionState,
  shouldStepBack,
  toggleSelection,
  withFilters,
  withOffset,
  withPage,
} from '@/domains/review/domain/public'

import { aSummary } from './feedback-entry.fixture'

const rejected = aSummary('r1', 'rejected')
const alsoRejected = aSummary('r2', 'rejected')
const approved = aSummary('a1', 'approved')

const loaded = withPage(emptyReviewQueue, {
  entries: [rejected, approved, alsoRejected],
  total: 3,
  offset: 0,
})

describe('ReviewQueue paging', () => {
  it('starts on the first page of fifty', () => {
    expect(emptyReviewQueue.page).toEqual({ limit: 50, offset: 0 })
  })

  // FR-REV-002: any filter change resets the offset, or the reviewer lands on a page that the new
  // filter may not even have.
  it('returns to the first page whenever a filter changes', () => {
    const paged = withOffset(createReviewQueue(noFilters, { limit: 50, offset: 100 }), 100)

    expect(withFilters(paged, { ...noFilters, rating: 'negative' }).page.offset).toBe(0)
  })

  it('drops the selection when the page or filters change', () => {
    const selected = toggleSelection(loaded, rejected.id)

    expect(selected.selection.size).toBe(1)
    expect(withOffset(selected, 50).selection.size).toBe(0)
    expect(withFilters(selected, noFilters).selection.size).toBe(0)
  })

  // FR-REV-011c / FIX-16: a refetch is a new page, so nothing survives pointing at a deleted row.
  it('drops the selection on refetch', () => {
    const selected = toggleSelection(loaded, rejected.id)

    expect(withPage(selected, { entries: [rejected], total: 1, offset: 0 }).selection.size).toBe(0)
  })

  it('never accepts a negative offset', () => {
    expect(withOffset(loaded, -20).page.offset).toBe(0)
  })

  it('exposes the window the pagination control renders', () => {
    expect(pageWindow(loaded)).toMatchObject({ from: 1, to: 3, total: 3 })
  })

  // FR-REV-011b: a page emptied by a bulk delete steps back rather than stranding the reviewer.
  it('asks to step back only from an emptied page that is not the first', () => {
    const emptiedDeep = withPage(createReviewQueue(noFilters, { limit: 50, offset: 50 }), {
      entries: [],
      total: 40,
      offset: 50,
    })

    expect(shouldStepBack(emptiedDeep)).toBe(true)
    expect(shouldStepBack(withPage(emptyReviewQueue, { entries: [], total: 0, offset: 0 }))).toBe(false)
    expect(shouldStepBack(loaded)).toBe(false)
  })
})

describe('ReviewQueue selection', () => {
  // The aggregate invariant: only rejected entries may be selected (FR-REV-011).
  it('refuses to select an entry that is not rejected', () => {
    expect(isSelectable(loaded, approved.id)).toBe(false)
    expect(toggleSelection(loaded, approved.id).selection.size).toBe(0)
  })

  it('ignores an id that is not on the page', () => {
    expect(toggleSelection(loaded, feedbackEntryId('absent')).selection.size).toBe(0)
  })

  it('toggles a rejected entry on and off', () => {
    const once = toggleSelection(loaded, rejected.id)
    expect([...once.selection]).toEqual([rejected.id])
    expect(toggleSelection(once, rejected.id).selection.size).toBe(0)
  })

  it('selects every rejected entry and no others', () => {
    expect([...selectAllSelectable(loaded).selection]).toEqual([rejected.id, alsoRejected.id])
  })

  it('clears the selection', () => {
    expect(clearSelection(selectAllSelectable(loaded)).selection.size).toBe(0)
  })

  it('reports the header control state, counting only selectable rows', () => {
    expect(selectionState(loaded)).toBe('none')
    expect(selectionState(toggleSelection(loaded, rejected.id))).toBe('partial')
    expect(selectionState(selectAllSelectable(loaded))).toBe('all')
  })

  it('is `none` on a page with nothing selectable, never `all`', () => {
    const noneSelectable = withPage(emptyReviewQueue, { entries: [approved], total: 1, offset: 0 })

    expect(selectionState(selectAllSelectable(noneSelectable))).toBe('none')
  })
})
