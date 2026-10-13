import { CalendarDate } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

import type { ReviewQuery } from '@/domains/review/application/ports'
import type { Rating, ReviewStatus } from '@/domains/review/domain/public'

export type ReviewSearchInput = {
  readonly status: ReviewStatus | null
  readonly rating: Rating | null
  readonly tags?: string
  readonly from?: string
  readonly to?: string
  readonly offset: number
}

const date = (value: string | undefined): CalendarDate | null => {
  if (value === undefined) {
    return null
  }

  const parsed = CalendarDate.fromIso(value)
  return isOk(parsed) ? parsed.value : null
}

export const toReviewQuery = (search: ReviewSearchInput): ReviewQuery => ({
  status: search.status,
  rating: search.rating,
  tags: search.tags?.split(',').map((tag) => tag.trim()).filter(Boolean) ?? [],
  dateFrom: date(search.from),
  dateTo: date(search.to),
  limit: 50,
  offset: search.offset,
})