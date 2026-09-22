import type { Problem } from '@/shared/http/exception'

import type { CursorlessPage, DailyUsageRow, DateRange, ModelUsageRow, SessionUsageRow, UserUsageRow } from '@/domains/telemetry/domain/public'

export type TelemetryListStatus = 'idle' | 'loading' | 'ready' | 'error'

export type TelemetrySection<Row> = {
  readonly rows: readonly Row[]
  readonly status: TelemetryListStatus
  readonly problem: Problem | null
}

export type TelemetrySnapshot = {
  readonly summary: TelemetrySection<ModelUsageRow>
  readonly topUsers: TelemetrySection<UserUsageRow> & { readonly limit: number }
  readonly byDay: TelemetrySection<DailyUsageRow> & { readonly range: DateRange | null }
  readonly byUser: TelemetrySection<SessionUsageRow> & {
    readonly userId: string
    readonly page: CursorlessPage
  }
}

export type TelemetryStoreOptions = {
  readonly gateway: import('@/domains/telemetry/application/ports').TelemetryGateway
}

const emptySection = <Row>(): TelemetrySection<Row> => ({
  rows: [],
  status: 'idle',
  problem: null,
})

export const TELEMETRY_DEFAULT_TOP_USERS_LIMIT = 10
export const TELEMETRY_DEFAULT_BY_USER_LIMIT = 50

export const createInitialTelemetrySnapshot = (): TelemetrySnapshot => ({
  summary: emptySection<ModelUsageRow>(),
  topUsers: {
    ...emptySection<UserUsageRow>(),
    limit: TELEMETRY_DEFAULT_TOP_USERS_LIMIT,
  },
  byDay: {
    ...emptySection<DailyUsageRow>(),
    range: null,
  },
  byUser: {
    ...emptySection<SessionUsageRow>(),
    userId: '',
    page: {
      offset: 0,
      limit: TELEMETRY_DEFAULT_BY_USER_LIMIT,
      rowCount: 0,
      hasNext: false,
      hasPrev: false,
    },
  },
})