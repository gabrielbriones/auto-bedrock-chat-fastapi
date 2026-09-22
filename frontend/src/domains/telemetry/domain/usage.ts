import type { Instant, CalendarDate } from '@/shared/kernel/instant'
import { err, ok, type Result } from '@/shared/kernel/result'

export type TokenCount = {
  readonly input: number
  readonly output: number
  readonly total: number
}

export const createTokenCount = (input: number, output: number): TokenCount => ({
  input,
  output,
  total: input + output,
})

export type ModelUsageRow = {
  readonly modelId: string
  readonly tokens: TokenCount
  readonly turnCount: number
}

export type UserUsageRow = {
  readonly userId: string
  readonly tokens: TokenCount
}

export type DailyUsageRow = {
  readonly date: CalendarDate
  readonly tokens: TokenCount
  readonly turnCount: number
}

export type SessionUsageRow = {
  readonly sessionId: string
  readonly modelId: string
  readonly tokens: TokenCount
  readonly turnAt: Instant
}

export type DateRange = {
  readonly start: CalendarDate
  readonly end: CalendarDate
}

export type DateRangeError = {
  readonly code: 'end-not-after-start'
  readonly message: 'End date must be after start date.'
}

export const createDateRange = (
  start: CalendarDate,
  end: CalendarDate,
): Result<DateRange, DateRangeError> =>
  start.compare(end) < 0
    ? ok({ start, end })
    : err({
        code: 'end-not-after-start',
        message: 'End date must be after start date.',
      })

export type CursorlessPage = {
  readonly offset: number
  readonly limit: number
  readonly rowCount: number
  readonly hasNext: boolean
  readonly hasPrev: boolean
}

export const createCursorlessPage = (
  offset: number,
  limit: number,
  rowCount: number,
): CursorlessPage => ({
  offset,
  limit,
  rowCount,
  hasNext: rowCount === limit,
  hasPrev: offset > 0,
})