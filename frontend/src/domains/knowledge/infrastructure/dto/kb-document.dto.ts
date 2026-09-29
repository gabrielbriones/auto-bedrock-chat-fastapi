import { z } from 'zod'

import { kbDocumentId } from '@/shared/kernel/branded'
import { invalidResponseProblem, type Problem } from '@/shared/http/exception'
import { instantSchema, issuesOf, nullableInstantSchema, nullableTextSchema } from '@/shared/http/wire'
import { CalendarDate } from '@/shared/kernel/instant'
import { isOk, ok, err, type Result } from '@/shared/kernel/result'
import type { RollbackResult } from '@/shared/kernel/curation'

import type { KbQuery } from '@/domains/knowledge/application/ports'
import type { KbDocument, KbDocumentDraft, KbDocumentSummary, SparsePatch } from '@/domains/knowledge/domain/public'
import { createCredibility, diffDocument, toSummary } from '@/domains/knowledge/domain/public'

const nullableCalendarDateSchema = z.string().nullish().transform((value, context) => {
  if (value === null || value === undefined) {
    return null
  }

  const parsed = CalendarDate.fromIso(value)
  if (!isOk(parsed)) {
    context.addIssue({ code: 'custom', message: `not a calendar date: ${value}` })
    return z.NEVER
  }

  return parsed.value
})

const nullableRecordSchema = z
  .record(z.string(), z.unknown())
  .nullish()
  .transform((value) => value ?? {})

const nullableTagsSchema = z
  .array(z.string())
  .nullish()
  .transform((value) => value ?? [])

export const kbDocumentDtoSchema = z.object({
  id: z.string().min(1),
  content: z.string().nullish().transform((value) => value ?? null),
  title: nullableTextSchema,
  source: nullableTextSchema,
  source_url: nullableTextSchema,
  topic: nullableTextSchema,
  date_published: nullableCalendarDateSchema,
  metadata: nullableRecordSchema,
  tags: nullableTagsSchema,
  chunk_count: z.number().int().min(0).nullish().transform((value) => value ?? null),
  created_at: nullableInstantSchema,
  credibility_score: z.number().min(0).max(1).nullish().transform((value) => value ?? 1),
  removal_flagged: z.boolean().nullish().transform((value) => value ?? false),
})

export const kbDocumentListDtoSchema = z.object({
  items: z.array(kbDocumentDtoSchema),
  total: z.number().int().min(0),
  limit: z.number().int().min(1),
  offset: z.number().int().min(0),
})

const rollbackDtoSchema = z.object({
  article_id: z.string().min(1),
  rolled_back_at: instantSchema,
  rolled_back_by: z.string(),
  reason: nullableTextSchema,
  feedback_entries_reverted: z.number().int().min(0),
})

type KbDocumentDto = z.output<typeof kbDocumentDtoSchema>

const mapDocument = (dto: KbDocumentDto): KbDocument => ({
  id: kbDocumentId(dto.id),
  title: dto.title,
  source: dto.source,
  sourceUrl: dto.source_url,
  topic: dto.topic,
  tags: dto.tags,
  content: dto.content,
  datePublished: dto.date_published,
  metadata: dto.metadata,
  chunkCount: dto.chunk_count,
  createdAt: dto.created_at,
  credibility: createCredibility(dto.credibility_score, dto.removal_flagged),
})

export const toKbDocument = (value: unknown): Result<KbDocument, Problem> => {
  const parsed = kbDocumentDtoSchema.safeParse(value)

  return parsed.success
    ? ok(mapDocument(parsed.data))
    : err(invalidResponseProblem('Invalid knowledge-base document', issuesOf(parsed.error)))
}

export const toKbDocumentPage = (value: unknown): Result<{
  readonly items: readonly KbDocumentSummary[]
  readonly total: number
  readonly limit: number
  readonly offset: number
}, Problem> => {
  const parsed = kbDocumentListDtoSchema.safeParse(value)

  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid knowledge-base document list', issuesOf(parsed.error)))
  }

  return ok({
    items: parsed.data.items.map(mapDocument).map(toSummary),
    total: parsed.data.total,
    limit: parsed.data.limit,
    offset: parsed.data.offset,
  })
}

export const toRollbackResult = (value: unknown): Result<RollbackResult, Problem> => {
  const parsed = rollbackDtoSchema.safeParse(value)

  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid knowledge-base rollback result', issuesOf(parsed.error)))
  }

  return ok({
    kbDocumentId: kbDocumentId(parsed.data.article_id),
    rolledBackAt: parsed.data.rolled_back_at,
  })
}

export type KbUpdateBody = {
  readonly title?: string | null
  readonly topic?: string | null
  readonly tags?: readonly string[]
  readonly content?: string
  readonly date_published?: string | null
  readonly metadata?: Readonly<Record<string, unknown>>
}

export const fromSparsePatch = (patch: SparsePatch): KbUpdateBody => ({
  ...(patch.title !== undefined ? { title: patch.title } : {}),
  ...(patch.topic !== undefined ? { topic: patch.topic } : {}),
  ...(patch.tags !== undefined ? { tags: [...patch.tags] } : {}),
  ...(patch.content !== undefined ? { content: patch.content } : {}),
  ...(patch.datePublished !== undefined ? { date_published: patch.datePublished?.toIso() ?? null } : {}),
  ...(patch.metadata !== undefined ? { metadata: patch.metadata } : {}),
})

export const encodeKbQuery = (query: KbQuery): string => {
  const parts: string[] = []
  const append = (key: string, value: string): void => {
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
  }

  if (query.source !== null) {
    append('source', query.source)
  }

  if (query.topic !== null) {
    append('topic', query.topic)
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

  if (query.removalFlagged) {
    append('removal_flagged', 'true')
  }

  append('limit', String(query.limit))
  append('offset', String(query.offset))

  return parts.join('&')
}

export const toDraft = (document: KbDocument): KbDocumentDraft => ({
  title: document.title,
  topic: document.topic,
  tags: document.tags,
  content: document.content,
  datePublished: document.datePublished,
  metadata: document.metadata,
})

export { diffDocument }