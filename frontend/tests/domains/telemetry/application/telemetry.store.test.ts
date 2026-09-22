import { describe, expect, it, vi } from 'vitest'

import { err, isErr, ok, type Result } from '@/shared/kernel/result'
import type { Problem } from '@/shared/http/exception'
import type { TelemetryGateway } from '@/domains/telemetry/application/ports'
import {
  createCursorlessPage,
  createDateRange,
  createTokenCount,
  type ModelUsageRow,
} from '@/domains/telemetry/domain/public'
import { CalendarDate } from '@/shared/kernel/instant'
import { TelemetryStore } from '@/domains/telemetry/application/telemetry.store'

const problem = (overrides: Partial<Problem> = {}): Problem => ({
  code: 'http-error',
  title: 'Request failed',
  ...overrides,
})

const date = (value: string) => {
  const result = CalendarDate.fromIso(value)
  if (isErr(result)) throw new Error(`invalid test date: ${value}`)
  return result.value
}

const range = createDateRange(date('2026-05-01'), date('2026-05-31'))

if (isErr(range)) throw new Error('test range must be valid')

const createGateway = (): TelemetryGateway => ({
  summary: vi.fn(async () => ok([])),
  topUsers: vi.fn(async () => ok([])),
  byDay: vi.fn(async () => ok([])),
  byUser: vi.fn(async () => ok([])),
})

describe('TelemetryStore', () => {
  it('keeps section failures independent', async () => {
    const gateway = createGateway()
    gateway.summary = vi.fn(async () => err(problem()))
    gateway.topUsers = vi.fn(async () => ok([{ userId: 'alice', tokens: createTokenCount(2, 3) }]))
    const store = new TelemetryStore({ gateway })

    await Promise.all([store.loadSummary(), store.loadTopUsers(10)])

    expect(store.getSnapshot().summary.status).toBe('error')
    expect(store.getSnapshot().topUsers).toMatchObject({ status: 'ready', limit: 10 })
    expect(store.getSnapshot().topUsers.rows).toHaveLength(1)
  })

  it('ignores an older response after a newer request starts', async () => {
    const gateway = createGateway()
    type SummaryResult = Result<readonly ModelUsageRow[], Problem>
    const responses: Array<{
      readonly resolve: (value: SummaryResult) => void
      readonly promise: Promise<SummaryResult>
    }> = []
    gateway.summary = vi.fn((): Promise<SummaryResult> => {
      let resolve!: (value: SummaryResult) => void
      const promise = new Promise<SummaryResult>((next) => { resolve = next })
      responses.push({ resolve, promise })
      return promise
    })
    const store = new TelemetryStore({ gateway })

    const first = store.loadSummary()
    const second = store.loadSummary()
    responses[1]?.resolve(ok([{ modelId: 'new', tokens: createTokenCount(1, 1), turnCount: 1 }]))
    await second
    responses[0]?.resolve(err(problem()))
    await first

    expect(store.getSnapshot().summary.rows[0]?.modelId).toBe('new')
    expect(store.getSnapshot().summary.status).toBe('ready')
  })

  it('refetches only top users when its limit changes', async () => {
    const gateway = createGateway()
    const store = new TelemetryStore({ gateway })

    await store.loadSummary()
    await store.loadTopUsers(5)

    expect(gateway.summary).toHaveBeenCalledTimes(1)
    expect(gateway.topUsers).toHaveBeenCalledWith(5, expect.any(AbortSignal))
  })

  it('returns a rejected server date range to idle and does not reissue it', async () => {
    const gateway = createGateway()
    gateway.byDay = vi.fn(async () => err(problem({ status: 400, serverCode: 'invalid_date_range' })))
    const store = new TelemetryStore({ gateway })

    await store.loadByDay(range.value)
    await store.loadByDay(range.value)

    expect(gateway.byDay).toHaveBeenCalledTimes(1)
    expect(store.getSnapshot().byDay).toMatchObject({ status: 'idle', range: null })
    expect(store.getSnapshot().byDay.problem?.serverCode).toBe('invalid_date_range')
  })

  it('derives cursorless navigation from the returned by-user row count', async () => {
    const gateway = createGateway()
    gateway.byUser = vi.fn(async () =>
      ok([
        {
          sessionId: 'session-1',
          modelId: 'claude-3',
          tokens: createTokenCount(2, 3),
          turnAt: { epochMilliseconds: 1, compare: () => 0, equals: () => false, toIso: () => '1970-01-01T00:00:00.001Z' } as never,
        },
      ]),
    )
    const store = new TelemetryStore({ gateway })

    await store.loadByUser({ userId: 'alice', page: createCursorlessPage(50, 50, 50) })

    expect(store.getSnapshot().byUser.page).toMatchObject({ offset: 50, rowCount: 1, hasNext: false, hasPrev: true })
  })
})