import { z } from 'zod'

import { invalidResponseProblem, type Problem } from '@/shared/http/exception'
import { err, ok, type Result } from '@/shared/kernel/result'

import type { FeedbackStats } from '@/domains/review/application/ports'
import { issuesOf } from '@/domains/review/infrastructure/dto/wire'

// Live shape (`FeedbackStats` in the running server's OpenAPI): `by_status`/`by_rating` are maps
// keyed by the enum values, and the server also reports `with_correction` and `integrated_count`,
// neither of which CONTRACT-001 mentions.
export const feedbackStatsDtoSchema = z.object({
  total: z.number().default(0),
  by_status: z.record(z.string(), z.number()).default({}),
  by_rating: z.record(z.string(), z.number()).default({}),
  with_correction: z.number().default(0),
  top_tags: z.array(z.object({ tag: z.string(), count: z.number() })).default([]),
  oldest_pending_hours: z
    .number()
    .nullish()
    .transform((value) => value ?? null),
  integrated_count: z.number().default(0),
})

// FR-REV-017: a bucket the server omits is genuinely zero, not missing data.
const bucket = (counts: Record<string, number>, key: string): number => counts[key] ?? 0

export const toFeedbackStats = (value: unknown): Result<FeedbackStats, Problem> => {
  const parsed = feedbackStatsDtoSchema.safeParse(value)

  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid feedback statistics', issuesOf(parsed.error)))
  }

  const dto = parsed.data

  return ok({
    total: dto.total,
    pendingReview: bucket(dto.by_status, 'pending_review'),
    approved: bucket(dto.by_status, 'approved'),
    rejected: bucket(dto.by_status, 'rejected'),
    positive: bucket(dto.by_rating, 'positive'),
    negative: bucket(dto.by_rating, 'negative'),
    withCorrection: dto.with_correction,
    integrated: dto.integrated_count,
    oldestPendingHours: dto.oldest_pending_hours,
    topTags: dto.top_tags,
  })
}
