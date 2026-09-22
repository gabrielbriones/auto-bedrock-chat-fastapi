import { z } from 'zod'

import { feedbackEntryId, kbDocumentId, userId } from '@/shared/kernel/branded'
import { invalidResponseProblem, type Problem } from '@/shared/http/exception'
import { err, ok, type Result } from '@/shared/kernel/result'

import {
  deriveSynthesisState,
  toSummary,
  type FeedbackEntry,
  type FeedbackEntrySummary,
  type KbSource,
  type ReviewMessage,
  type ReviewerDecision,
} from '@/domains/review/domain/public'
import type { Page } from '@/domains/review/application/ports'
import { instantSchema, issuesOf, nullableInstantSchema, nullableTextSchema } from '@/domains/review/infrastructure/dto/wire'

// Verified against the live backend's OpenAPI document (`GET /openapi.json` on the running dev
// server, 2026-09-08), not CONTRACT-001's prose — which omits `session_id` and `score` and treats
// `model_id`/`ai_response` as optional when the server declares all four differently.
const ratingSchema = z.enum(['positive', 'negative'])

const reviewStatusSchema = z.enum(['pending_review', 'approved', 'rejected'])

// `conversation_history` and `kb_sources_used` are `Dict[str, Any]` on the server, so their inner
// keys carry no guarantee. They are read leniently and normalised here rather than rejected —
// losing the whole entry because one history frame lacks a role would be a worse failure.
const asRecord = (value: unknown): Record<string, unknown> =>
  typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}

const asText = (value: unknown): string | null =>
  typeof value === 'string' && value.length > 0 ? value : null

const reviewMessageSchema = z.unknown().transform((value): ReviewMessage => {
  const raw = asRecord(value)

  return {
    role: typeof raw.role === 'string' && raw.role.length > 0 ? raw.role : 'assistant',
    content: typeof raw.content === 'string' ? raw.content : '',
  }
})

const kbSourceSchema = z.unknown().transform((value): KbSource => {
  const raw = asRecord(value)

  return {
    documentId: asText(raw.document_id),
    title: asText(raw.title),
    source: asText(raw.source),
    url: asText(raw.url),
    score: typeof raw.score === 'number' ? raw.score : null,
  }
})

export const feedbackEntryDtoSchema = z.object({
  id: z.string().min(1),
  session_id: z.string(),
  user_id: z.string().min(1),
  query: z.string(),
  ai_response: z.string(),
  rating: ratingSchema,
  correction_text: nullableTextSchema,
  user_comment: nullableTextSchema,
  conversation_history: z.array(reviewMessageSchema).default([]),
  kb_sources_used: z.array(kbSourceSchema).default([]),
  model_id: z.string(),
  entry_metadata: z.record(z.string(), z.unknown()).default({}),
  review_status: reviewStatusSchema,
  reviewer_id: nullableTextSchema,
  reviewer_tags: z.array(z.string()).default([]),
  reviewer_comment: nullableTextSchema,
  reviewed_at: nullableInstantSchema,
  integrated_into_kb_id: nullableTextSchema,
  integrated_at: nullableInstantSchema,
  rolled_back_at: nullableInstantSchema,
  rolled_back_by: nullableTextSchema,
  rollback_reason: nullableTextSchema,
  created_at: instantSchema,
})

export const feedbackListDtoSchema = z.object({
  items: z.array(feedbackEntryDtoSchema),
  total: z.number().int().min(0),
  limit: z.number().int().min(1),
  offset: z.number().int().min(0),
})

type FeedbackEntryDto = z.output<typeof feedbackEntryDtoSchema>

// The server only sets `reviewer_id`/`reviewed_at` once a decision exists, so their absence is
// what distinguishes "never reviewed" from "reviewed with no comment".
const toReviewerDecision = (dto: FeedbackEntryDto): ReviewerDecision | null =>
  dto.review_status === 'pending_review' && dto.reviewed_at === null
    ? null
    : {
        reviewerId: dto.reviewer_id,
        reviewedAt: dto.reviewed_at,
        tags: dto.reviewer_tags,
        comment: dto.reviewer_comment,
      }

const mapEntry = (dto: FeedbackEntryDto): FeedbackEntry => ({
  id: feedbackEntryId(dto.id),
  userId: userId(dto.user_id),
  sessionId: dto.session_id,
  rating: dto.rating,
  reviewStatus: dto.review_status,
  query: dto.query,
  createdAt: dto.created_at,
  reviewerTags: dto.reviewer_tags,
  modelId: dto.model_id,
  aiResponse: dto.ai_response,
  correctionText: dto.correction_text,
  userComment: dto.user_comment,
  kbSourcesUsed: dto.kb_sources_used,
  entryMetadata: dto.entry_metadata,
  conversationHistory: dto.conversation_history,
  review: toReviewerDecision(dto),
  synthesis: deriveSynthesisState({
    kbDocumentId: dto.integrated_into_kb_id === null ? null : kbDocumentId(dto.integrated_into_kb_id),
    integratedAt: dto.integrated_at,
    rolledBackAt: dto.rolled_back_at,
    rolledBackBy: dto.rolled_back_by,
    rollbackReason: dto.rollback_reason,
  }),
})

// DESIGN-001 §8: a mapper never throws. Every failure is an `invalid-response` Problem carrying
// the schema issues, so a contract drift is debuggable from the log line alone.
export const toFeedbackEntry = (value: unknown): Result<FeedbackEntry, Problem> => {
  const parsed = feedbackEntryDtoSchema.safeParse(value)

  return parsed.success
    ? ok(mapEntry(parsed.data))
    : err(invalidResponseProblem('Invalid feedback entry', issuesOf(parsed.error)))
}

export const toFeedbackEntryPage = (value: unknown): Result<Page<FeedbackEntrySummary>, Problem> => {
  const parsed = feedbackListDtoSchema.safeParse(value)

  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid feedback list', issuesOf(parsed.error)))
  }

  return ok({
    items: parsed.data.items.map((dto) => toSummary(mapEntry(dto))),
    total: parsed.data.total,
    limit: parsed.data.limit,
    offset: parsed.data.offset,
  })
}

// FR-REV-020 probes the list endpoint with `limit=1` purely for its `total`, so the badge never
// pays for fifty rows it will not show.
export const toTotalCount = (value: unknown): Result<number, Problem> => {
  const parsed = feedbackListDtoSchema.safeParse(value)

  return parsed.success
    ? ok(parsed.data.total)
    : err(invalidResponseProblem('Invalid feedback list', issuesOf(parsed.error)))
}
