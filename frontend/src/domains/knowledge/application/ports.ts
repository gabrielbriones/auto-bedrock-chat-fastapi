import type { KbDocumentId } from '@/shared/kernel/branded'
import type { Problem } from '@/shared/http/exception'
import type { CalendarDate } from '@/shared/kernel/instant'
import type { OffsetPage } from '@/shared/kernel/pagination'
import type { Result } from '@/shared/kernel/result'
import type { RollbackResult } from '@/shared/kernel/curation'

import type {
  FileIngestRequest,
  KbDocument,
  KbDocumentSummary,
  KbSourceRun,
  KbSourceSummary,
  SparsePatch,
  WebCrawlRequest,
} from '@/domains/knowledge/domain/public'

export type KbQuery = {
  readonly source: string | null
  readonly topic: string | null
  readonly tags: readonly string[]
  readonly dateFrom: CalendarDate | null
  readonly dateTo: CalendarDate | null
  readonly removalFlagged: boolean
} & OffsetPage

export type Page<T> = {
  readonly items: readonly T[]
  readonly total: number
  readonly limit: number
  readonly offset: number
}

export interface KnowledgeGateway {
  list(query: KbQuery, signal: AbortSignal): Promise<Result<Page<KbDocumentSummary>, Problem>>
  get(id: KbDocumentId, signal: AbortSignal): Promise<Result<KbDocument, Problem>>
  patch(id: KbDocumentId, patch: SparsePatch): Promise<Result<KbDocument, Problem>>
  remove(id: KbDocumentId): Promise<Result<void, Problem>>
  resetCredibility(id: KbDocumentId): Promise<Result<KbDocument, Problem>>
  rollback(id: KbDocumentId, reason: string | null): Promise<Result<RollbackResult, Problem>>
}

export type KbSourceDeletion = {
  readonly source: string
  readonly deleted: number | null
}

// KB source ingestion (admin-api.md "KB Source Ingestion"). `start*` is the `POST` that refuses a
// duplicate name with `409 source_already_exists`; `override*` is the matching `PUT .../{name}`
// that deletes the name's documents first and then runs the same ingestion.
export interface KbSourcesGateway {
  listSources(signal: AbortSignal): Promise<Result<readonly KbSourceSummary[], Problem>>
  status(signal: AbortSignal): Promise<Result<KbSourceRun, Problem>>
  startWebCrawl(request: WebCrawlRequest): Promise<Result<KbSourceRun, Problem>>
  overrideWebCrawl(request: WebCrawlRequest): Promise<Result<KbSourceRun, Problem>>
  startFileIngest(request: FileIngestRequest): Promise<Result<KbSourceRun, Problem>>
  overrideFileIngest(request: FileIngestRequest): Promise<Result<KbSourceRun, Problem>>
  deleteSource(name: string): Promise<Result<KbSourceDeletion, Problem>>
}

export type Cancel = () => void

// The one seam through which the store waits between status polls, so a test drives a whole run
// to completion without real time passing.
export interface PollScheduler {
  schedule(callback: () => void, delayMs: number): Cancel
}
