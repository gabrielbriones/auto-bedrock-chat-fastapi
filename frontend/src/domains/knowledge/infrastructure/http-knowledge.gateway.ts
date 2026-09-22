import type { KbDocumentId } from '@/shared/kernel/branded'
import type { HttpClient } from '@/shared/http/http-client'
import type { Problem } from '@/shared/http/exception'
import { andThen, ok, type Result } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

import type { KnowledgeGateway, KbQuery, Page } from '@/domains/knowledge/application/ports'
import type { KbDocument, KbDocumentSummary, SparsePatch } from '@/domains/knowledge/domain/public'
import {
  fromSparsePatch,
  toKbDocument,
  toKbDocumentPage,
  toRollbackResult,
  encodeKbQuery,
} from '@/domains/knowledge/infrastructure/dto/kb-document.dto'
import type { RollbackResult } from '@/shared/kernel/curation'

type KnowledgeHttpClient = Pick<HttpClient, 'request'>

const JSON_HEADERS = { 'content-type': 'application/json' } as const

export class HttpKnowledgeGateway implements KnowledgeGateway {
  private readonly adminPrefix: string
  private readonly httpClient: KnowledgeHttpClient
  private readonly logger: Logger

  constructor(adminPrefix: string, httpClient: KnowledgeHttpClient, logger: Logger) {
    this.adminPrefix = adminPrefix
    this.httpClient = httpClient
    this.logger = logger
  }

  async list(query: KbQuery, signal: AbortSignal): Promise<Result<Page<KbDocumentSummary>, Problem>> {
    const response = await this.read(`/kb/documents?${encodeKbQuery(query)}`, signal)

    return andThen(response, toKbDocumentPage)
  }

  async get(id: KbDocumentId, signal: AbortSignal): Promise<Result<KbDocument, Problem>> {
    const response = await this.read(`/kb/documents/${encodeURIComponent(id)}`, signal)

    return andThen(response, toKbDocument)
  }

  async patch(id: KbDocumentId, patch: SparsePatch): Promise<Result<KbDocument, Problem>> {
    const response = await this.write('PATCH', `/kb/documents/${encodeURIComponent(id)}`, fromSparsePatch(patch))

    return andThen(response, toKbDocument)
  }

  async remove(id: KbDocumentId): Promise<Result<void, Problem>> {
    const response = await this.httpClient.request<unknown>(this.url(`/kb/documents/${encodeURIComponent(id)}`), {
      method: 'DELETE',
      credentials: 'include',
      logger: this.logger,
    })

    return andThen(response, () => ok(undefined))
  }

  async resetCredibility(id: KbDocumentId): Promise<Result<KbDocument, Problem>> {
    const response = await this.write('POST', `/kb/documents/reset-credibility/${encodeURIComponent(id)}`, {})

    return andThen(response, toKbDocument)
  }

  async rollback(id: KbDocumentId, reason: string | null): Promise<Result<RollbackResult, Problem>> {
    const response = await this.write('POST', `/synthesis/rollback/${encodeURIComponent(id)}`, { reason })

    return andThen(response, toRollbackResult)
  }

  private url(path: string): string {
    return `${this.adminPrefix}${path}`
  }

  private read(path: string, signal: AbortSignal): Promise<Result<unknown, Problem>> {
    return this.httpClient.request<unknown>(this.url(path), {
      signal,
      credentials: 'include',
      logger: this.logger,
    })
  }

  private write(method: 'POST' | 'PATCH', path: string, body: unknown): Promise<Result<unknown, Problem>> {
    return this.httpClient.request<unknown>(this.url(path), {
      method,
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
      credentials: 'include',
      logger: this.logger,
    })
  }
}