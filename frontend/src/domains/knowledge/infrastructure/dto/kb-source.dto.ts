import { z } from 'zod'

import { invalidResponseProblem, type Problem } from '@/shared/http/exception'
import { issuesOf, nullableInstantSchema, nullableTextSchema } from '@/shared/http/wire'
import { err, ok, type Result } from '@/shared/kernel/result'

import type { KbSourceDeletion, Page } from '@/domains/knowledge/application/ports'
import type {
  FileIngestRequest,
  KbSourceRun,
  KbSourceSummary,
  WebCrawlRequest,
} from '@/domains/knowledge/domain/public'

const countSchema = z.number().int().min(0).nullish().transform((value) => value ?? 0)

export const kbSourceSummaryDtoSchema = z.object({
  source: z.string().min(1),
  source_type: z.enum(['web', 'file', 'feedback']).nullable(),
  document_count: z.number().int().min(0),
  chunk_count: z.number().int().min(0),
  last_created_at: nullableInstantSchema,
})

export const kbSourceSummaryListDtoSchema = z.object({
  items: z.array(kbSourceSummaryDtoSchema),
  total: z.number().int().min(0),
  limit: z.number().int().min(1),
  offset: z.number().int().min(0),
})

export const kbSourceRunDtoSchema = z.object({
  run_id: nullableTextSchema,
  phase: z.enum(['idle', 'running', 'completed', 'failed']).nullish().transform((value) => value ?? 'idle'),
  source_name: nullableTextSchema,
  source_type: z.enum(['web', 'file']).nullish().transform((value) => value ?? null),
  started_at: nullableInstantSchema,
  finished_at: nullableInstantSchema,
  pages_crawled: countSchema,
  pages_processed: countSchema,
  files_processed: countSchema,
  chunks_written: countSchema,
  error: nullableTextSchema,
  errors: z.array(z.string()).nullish().transform((value) => value ?? []),
})

const kbSourceDeletionDtoSchema = z.object({
  source: z.string(),
  deleted: z.number().int().min(0).nullish().transform((value) => value ?? null),
})

export const toKbSourceSummaries = (value: unknown): Result<Page<KbSourceSummary>, Problem> => {
  const parsed = kbSourceSummaryListDtoSchema.safeParse(value)

  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid knowledge-base source list', issuesOf(parsed.error)))
  }

  return ok({
    items: parsed.data.items.map((row) => ({
      source: row.source,
      sourceType: row.source_type,
      documentCount: row.document_count,
      chunkCount: row.chunk_count,
      lastCreatedAt: row.last_created_at,
    })),
    total: parsed.data.total,
    limit: parsed.data.limit,
    offset: parsed.data.offset,
  })
}

export const toKbSourceRun = (value: unknown): Result<KbSourceRun, Problem> => {
  const parsed = kbSourceRunDtoSchema.safeParse(value)

  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid knowledge-base source run status', issuesOf(parsed.error)))
  }

  const dto = parsed.data
  return ok({
    runId: dto.run_id,
    phase: dto.phase,
    sourceName: dto.source_name,
    sourceType: dto.source_type,
    startedAt: dto.started_at,
    finishedAt: dto.finished_at,
    pagesCrawled: dto.pages_crawled,
    pagesProcessed: dto.pages_processed,
    filesProcessed: dto.files_processed,
    chunksWritten: dto.chunks_written,
    error: dto.error,
    errors: dto.errors,
  })
}

export const toKbSourceDeletion = (value: unknown): Result<KbSourceDeletion, Problem> => {
  const parsed = kbSourceDeletionDtoSchema.safeParse(value)

  return parsed.success
    ? ok(parsed.data)
    : err(invalidResponseProblem('Invalid knowledge-base source deletion result', issuesOf(parsed.error)))
}

export type WebCrawlBody = {
  readonly name?: string
  readonly urls: readonly string[]
  readonly topic?: string
  readonly max_depth?: number
  readonly max_pages?: number
  readonly allowed_domains?: readonly string[]
  readonly exclude_patterns?: readonly string[]
  readonly ingest_linked_files?: true
  readonly synthesize?: true
  readonly headers?: Readonly<Record<string, string>>
  readonly cookies?: Readonly<Record<string, string>>
}

// `POST` carries `name` in the body; `PUT .../{name}` takes it from the path and its body model
// forbids extra keys, so the same request serialises two ways. Optional fields are omitted rather
// than sent as null so the server's defaults apply.
export const fromWebCrawlRequest = (request: WebCrawlRequest, withName: boolean): WebCrawlBody => ({
  ...(withName ? { name: request.name } : {}),
  urls: request.urls,
  ...(request.topic === null ? {} : { topic: request.topic }),
  ...(request.maxDepth === null ? {} : { max_depth: request.maxDepth }),
  ...(request.maxPages === null ? {} : { max_pages: request.maxPages }),
  ...(request.allowedDomains === null ? {} : { allowed_domains: request.allowedDomains }),
  ...(request.excludePatterns === null ? {} : { exclude_patterns: request.excludePatterns }),
  ...(request.ingestLinkedFiles ? { ingest_linked_files: true as const } : {}),
  ...(request.synthesize ? { synthesize: true as const } : {}),
  ...(request.headers === null ? {} : { headers: request.headers }),
  ...(request.cookies === null ? {} : { cookies: request.cookies }),
})

export const toFileIngestFormData = (request: FileIngestRequest, withName: boolean): FormData => {
  const formData = new FormData()

  if (withName) {
    formData.append('name', request.name)
  }
  if (request.topic !== null) {
    formData.append('topic', request.topic)
  }
  if (request.synthesize) {
    formData.append('synthesize', 'true')
  }
  for (const file of request.files) {
    formData.append('files', file, file.name)
  }

  return formData
}
