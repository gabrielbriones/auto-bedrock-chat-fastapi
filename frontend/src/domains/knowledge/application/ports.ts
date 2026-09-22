import type { KbDocumentId } from '@/shared/kernel/branded'
import type { Problem } from '@/shared/http/exception'
import type { CalendarDate } from '@/shared/kernel/instant'
import type { OffsetPage } from '@/shared/kernel/pagination'
import type { Result } from '@/shared/kernel/result'
import type { RollbackResult } from '@/shared/kernel/curation'

import type { KbDocument, KbDocumentSummary, SparsePatch } from '@/domains/knowledge/domain/public'

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