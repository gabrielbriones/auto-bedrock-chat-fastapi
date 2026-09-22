// Offset pagination arithmetic, shared by the `review` aggregate and the `OffsetPagination`
// primitive so the two can never disagree about whether a page has a way back (FIX-06).

export type OffsetPage = {
  readonly limit: number
  readonly offset: number
}

export type OffsetWindow = {
  /** 1-based index of the first row on this page; 0 when the page holds nothing. */
  readonly from: number
  /** 1-based index of the last row on this page; 0 when the page holds nothing. */
  readonly to: number
  readonly total: number
  readonly hasPrevious: boolean
  readonly hasNext: boolean
  readonly previousOffset: number
  readonly nextOffset: number
}

export const DEFAULT_PAGE_LIMIT = 50

const clampToZero = (value: number): number => (value > 0 ? value : 0)

// FIX-06: the legacy `renderPagination` returned early when `total` was 0, stranding anyone who
// had paged into a list that then shrank. Here an empty page at a non-zero offset still reports
// `hasPrevious`, so the controls are always an escape route rather than a dead end.
export const offsetWindow = (page: OffsetPage, total: number): OffsetWindow => {
  const limit = page.limit > 0 ? page.limit : DEFAULT_PAGE_LIMIT
  const offset = clampToZero(page.offset)
  const safeTotal = clampToZero(total)
  const from = offset < safeTotal ? offset + 1 : 0
  const to = from === 0 ? 0 : Math.min(offset + limit, safeTotal)

  return {
    from,
    to,
    total: safeTotal,
    hasPrevious: offset > 0,
    hasNext: offset + limit < safeTotal,
    previousOffset: clampToZero(offset - limit),
    nextOffset: offset + limit,
  }
}
