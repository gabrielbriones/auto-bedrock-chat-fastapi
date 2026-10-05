import type { HttpClient } from '@/shared/http/http-client'
import type { Problem } from '@/shared/http/exception'
import type { HttpMethod } from '@/shared/http/retry-policy'
import { andThen, type Result } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

import type { KbSourceDeletion, KbSourcesGateway } from '@/domains/knowledge/application/ports'
import type {
  FileIngestRequest,
  KbSourceRun,
  KbSourceSummary,
  WebCrawlRequest,
} from '@/domains/knowledge/domain/public'
import {
  fromWebCrawlRequest,
  toFileIngestFormData,
  toKbSourceDeletion,
  toKbSourceRun,
  toKbSourceSummaries,
} from '@/domains/knowledge/infrastructure/dto/kb-source.dto'

type KbSourcesHttpClient = Pick<HttpClient, 'request'>

const JSON_HEADERS = { 'content-type': 'application/json' } as const

// A UTF-8 hex segment cannot be normalized as "." or ".." by Fetch. Encode
// every name the same way so percent signs and names resembling identifiers
// cannot collide with an alternate spelling of another source.
const sourceId = (name: string): string =>
  `~${Array.from(new TextEncoder().encode(name), (byte) => byte.toString(16).padStart(2, '0')).join('')}`

// admin-api.md "KB Source Ingestion". Both `POST` and `PUT` answer `202` with the run status; the
// multipart routes leave `content-type` to the browser so the boundary is set correctly.
export class HttpKbSourcesGateway implements KbSourcesGateway {
  private readonly adminPrefix: string
  private readonly httpClient: KbSourcesHttpClient
  private readonly logger: Logger

  constructor(adminPrefix: string, httpClient: KbSourcesHttpClient, logger: Logger) {
    this.adminPrefix = adminPrefix
    this.httpClient = httpClient
    this.logger = logger
  }

  async listSources(signal: AbortSignal): Promise<Result<readonly KbSourceSummary[], Problem>> {
    const response = await this.read('/kb/sources', signal)

    return andThen(response, toKbSourceSummaries)
  }

  async status(signal: AbortSignal): Promise<Result<KbSourceRun, Problem>> {
    const response = await this.read('/kb/sources/status', signal)

    return andThen(response, toKbSourceRun)
  }

  async startWebCrawl(request: WebCrawlRequest): Promise<Result<KbSourceRun, Problem>> {
    const response = await this.writeJson('POST', '/kb/sources/web', fromWebCrawlRequest(request, true))

    return andThen(response, toKbSourceRun)
  }

  async overrideWebCrawl(request: WebCrawlRequest): Promise<Result<KbSourceRun, Problem>> {
    const response = await this.writeJson(
      'PUT',
      `/kb/sources/web/${encodeURIComponent(request.name)}`,
      fromWebCrawlRequest(request, false),
    )

    return andThen(response, toKbSourceRun)
  }

  async startFileIngest(request: FileIngestRequest): Promise<Result<KbSourceRun, Problem>> {
    const response = await this.writeForm('POST', '/kb/sources/file', toFileIngestFormData(request, true))

    return andThen(response, toKbSourceRun)
  }

  async overrideFileIngest(request: FileIngestRequest): Promise<Result<KbSourceRun, Problem>> {
    const response = await this.writeForm(
      'PUT',
      `/kb/sources/file/${encodeURIComponent(request.name)}`,
      toFileIngestFormData(request, false),
    )

    return andThen(response, toKbSourceRun)
  }

  async deleteSource(name: string): Promise<Result<KbSourceDeletion, Problem>> {
    const response = await this.httpClient.request<unknown>(
      this.url(`/kb/sources/${sourceId(name)}`),
      { method: 'DELETE', credentials: 'include', logger: this.logger },
    )

    return andThen(response, toKbSourceDeletion)
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

  private writeJson(method: HttpMethod, path: string, body: unknown): Promise<Result<unknown, Problem>> {
    return this.httpClient.request<unknown>(this.url(path), {
      method,
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
      credentials: 'include',
      logger: this.logger,
    })
  }

  private writeForm(method: HttpMethod, path: string, body: FormData): Promise<Result<unknown, Problem>> {
    return this.httpClient.request<unknown>(this.url(path), {
      method,
      body,
      credentials: 'include',
      logger: this.logger,
    })
  }
}
