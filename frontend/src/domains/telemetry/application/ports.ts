import type { Problem } from '@/shared/http/exception'
import type { Result } from '@/shared/kernel/result'

import type {
  CursorlessPage,
  DailyUsageRow,
  DateRange,
  ModelUsageRow,
  SessionUsageRow,
  UserUsageRow,
} from '@/domains/telemetry/domain/public'

export type ByUserUsageQuery = {
  readonly userId: string
  readonly page: CursorlessPage
}

export interface TelemetryGateway {
  summary(signal: AbortSignal): Promise<Result<readonly ModelUsageRow[], Problem>>
  topUsers(limit: number, signal: AbortSignal): Promise<Result<readonly UserUsageRow[], Problem>>
  byDay(range: DateRange, signal: AbortSignal): Promise<Result<readonly DailyUsageRow[], Problem>>
  byUser(query: ByUserUsageQuery, signal: AbortSignal): Promise<Result<readonly SessionUsageRow[], Problem>>
}