import type { FeedbackEntryId, KbDocumentId } from '@/shared/kernel/branded'
import type { Problem } from '@/shared/http/exception'
import type { HttpClient } from '@/shared/http/http-client'
import { andThen, ok, type Result } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

import type {
  FeedbackStats,
  Page,
  ReviewGateway,
  ReviewQuery,
  RollbackOutcome,
  SynthesisOutcome,
} from '@/domains/review/application/ports'
import type { FeedbackEntry, FeedbackEntrySummary, ReviewDecisionDraft, SynthesisPhase } from '@/domains/review/domain/public'
import {
  toFeedbackEntry,
  toFeedbackEntryPage,
  toTotalCount,
} from '@/domains/review/infrastructure/dto/feedback-entry.dto'
import { toFeedbackStats } from '@/domains/review/infrastructure/dto/feedback-stats.dto'
import {
  encodeReviewQuery,
  fromReviewDecision,
} from '@/domains/review/infrastructure/dto/review-decision.dto'
import {
  toRollbackOutcome,
  toSynthesisOutcome,
  toSynthesisPhase,
} from '@/domains/review/infrastructure/dto/synthesis.dto'

type ReviewHttpClient = Pick<HttpClient, 'request'>

const JSON_HEADERS = { 'content-type': 'application/json' } as const

// SPEC-016 §3 over `shared/http`. The adapter owns the paths and the credential mode; every body
// leaves through a mapper, so no wire shape reaches the application layer (CT-2, DESIGN-001 §8).
export class HttpReviewGateway implements ReviewGateway {
  private readonly adminPrefix: string
  private readonly httpClient: ReviewHttpClient
  private readonly logger: Logger

  constructor(adminPrefix: string, httpClient: ReviewHttpClient, logger: Logger) {
    this.adminPrefix = adminPrefix
    this.httpClient = httpClient
    this.logger = logger
  }

  async list(query: ReviewQuery, signal: AbortSignal): Promise<Result<Page<FeedbackEntrySummary>, Problem>> {
    const response = await this.read(`/feedback?${encodeReviewQuery(query)}`, signal)

    return andThen(response, toFeedbackEntryPage)
  }

  async get(id: FeedbackEntryId, signal: AbortSignal): Promise<Result<FeedbackEntry, Problem>> {
    const response = await this.read(`/feedback/${encodeURIComponent(id)}`, signal)

    return andThen(response, toFeedbackEntry)
  }

  async saveDecision(id: FeedbackEntryId, draft: ReviewDecisionDraft): Promise<Result<FeedbackEntry, Problem>> {
    const response = await this.write('PATCH', `/feedback/${encodeURIComponent(id)}`, fromReviewDecision(draft))

    return andThen(response, toFeedbackEntry)
  }

  async remove(id: FeedbackEntryId): Promise<Result<void, Problem>> {
    const response = await this.httpClient.request<unknown>(this.url(`/feedback/${encodeURIComponent(id)}`), {
      method: 'DELETE',
      credentials: 'include',
      logger: this.logger,
    })

    // 204 with no body: there is nothing to map, only a success to report.
    return andThen(response, () => ok(undefined))
  }

  async stats(signal: AbortSignal): Promise<Result<FeedbackStats, Problem>> {
    const response = await this.read('/feedback/stats', signal)

    return andThen(response, toFeedbackStats)
  }

  // FR-REV-020: the badge wants a count, so it asks for a single row and reads `total`.
  async pendingCount(signal: AbortSignal): Promise<Result<number, Problem>> {
    const response = await this.read('/feedback?status=pending_review&limit=1&offset=0', signal)

    return andThen(response, toTotalCount)
  }

  async synthesize(id: FeedbackEntryId): Promise<Result<SynthesisOutcome, Problem>> {
    const response = await this.write('POST', `/synthesis/trigger/${encodeURIComponent(id)}`, {})

    return andThen(response, toSynthesisOutcome)
  }

  async rollback(articleId: KbDocumentId, reason: string | null): Promise<Result<RollbackOutcome, Problem>> {
    const response = await this.write('POST', `/synthesis/rollback/${encodeURIComponent(articleId)}`, { reason })

    return andThen(response, toRollbackOutcome)
  }

  async synthesisPhase(signal: AbortSignal): Promise<Result<SynthesisPhase, Problem>> {
    const response = await this.read('/synthesis/status', signal)

    return andThen(response, toSynthesisPhase)
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
