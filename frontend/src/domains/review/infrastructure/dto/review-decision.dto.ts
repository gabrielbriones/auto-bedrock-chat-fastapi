import type { ReviewDecisionDraft } from '@/domains/review/domain/public'
import type { ReviewQuery } from '@/domains/review/application/ports'

export type ReviewUpdateBody = {
  readonly review_status: 'approved' | 'rejected'
  readonly reviewer_tags: readonly string[]
  readonly reviewer_comment: string | null
}

// FIX-05. The legacy client built this body by omitting empty values, so deleting the last tag or
// clearing a comment sent nothing and the server kept the old value — every clear was silently
// discarded. Both keys are therefore *always* present: an empty list and a `null` comment are the
// instructions to clear, and the server's `ReviewUpdateRequest` accepts exactly these three keys.
export const fromReviewDecision = (draft: ReviewDecisionDraft): ReviewUpdateBody => ({
  review_status: draft.decision,
  reviewer_tags: [...draft.tags],
  reviewer_comment: draft.comment,
})

// `URLSearchParams` is reserved for the router (FR-TOOL-019), so request queries are encoded here.
// Absent filters are omitted entirely rather than sent blank — the server reads a present-but-empty
// `status` as a validation error, not as "no constraint".
export const encodeReviewQuery = (query: ReviewQuery): string => {
  const parts: string[] = []
  const append = (key: string, value: string): void => {
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
  }

  if (query.status !== null) {
    append('status', query.status)
  }

  if (query.rating !== null) {
    append('rating', query.rating)
  }

  if (query.tags.length > 0) {
    append('tags', query.tags.join(','))
  }

  if (query.dateFrom !== null) {
    append('date_from', query.dateFrom.toIso())
  }

  if (query.dateTo !== null) {
    append('date_to', query.dateTo.toIso())
  }

  append('limit', String(query.limit))
  append('offset', String(query.offset))

  return parts.join('&')
}
