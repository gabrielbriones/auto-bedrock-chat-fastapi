import type { HttpClient } from '@/shared/http/http-client'
import type { Problem } from '@/shared/http/exception'
import { andThen, type Result } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

import type { TelemetryGateway, ByUserUsageQuery } from '@/domains/telemetry/application/ports'
import type {
  DailyUsageRow,
  DateRange,
  ModelUsageRow,
  SessionUsageRow,
  UserUsageRow,
} from '@/domains/telemetry/domain/public'
import {
  toDailyUsageRows,
  toModelUsageRows,
  toSessionUsageRows,
  toUserUsageRows,
  toUtcBoundary,
} from '@/domains/telemetry/infrastructure/dto/usage.dto'

type TelemetryHttpClient = Pick<HttpClient, 'request'>

export class HttpTelemetryGateway implements TelemetryGateway {
  private readonly adminPrefix: string
  private readonly httpClient: TelemetryHttpClient
  private readonly logger: Logger

  constructor(adminPrefix: string, httpClient: TelemetryHttpClient, logger: Logger) {
    this.adminPrefix = adminPrefix
    this.httpClient = httpClient
    this.logger = logger
  }

  async summary(signal: AbortSignal): Promise<Result<readonly ModelUsageRow[], Problem>> {
    const response = await this.read('/tokens/summary', signal)
    return andThen(response, toModelUsageRows)
  }

  async topUsers(limit: number, signal: AbortSignal): Promise<Result<readonly UserUsageRow[], Problem>> {
    const response = await this.read(`/tokens/top-users?limit=${encodeURIComponent(String(limit))}`, signal)
    return andThen(response, toUserUsageRows)
  }

  async byDay(range: DateRange, signal: AbortSignal): Promise<Result<readonly DailyUsageRow[], Problem>> {
    const query = `start=${encodeURIComponent(toUtcBoundary(range.start))}&end=${encodeURIComponent(toUtcBoundary(range.end))}`
    const response = await this.read(`/tokens/by-day?${query}`, signal)
    return andThen(response, toDailyUsageRows)
  }

  async byUser(query: ByUserUsageQuery, signal: AbortSignal): Promise<Result<readonly SessionUsageRow[], Problem>> {
    const params = [
      `user_id=${encodeURIComponent(query.userId)}`,
      `limit=${encodeURIComponent(String(query.page.limit))}`,
      `offset=${encodeURIComponent(String(query.page.offset))}`,
    ].join('&')
    const response = await this.read(`/tokens/by-user?${params}`, signal)
    return andThen(response, toSessionUsageRows)
  }

  private read(path: string, signal: AbortSignal): Promise<Result<unknown, Problem>> {
    return this.httpClient.request<unknown>(`${this.adminPrefix}${path}`, {
      signal,
      credentials: 'include',
      logger: this.logger,
    })
  }
}