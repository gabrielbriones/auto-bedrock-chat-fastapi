import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'

import { HttpClient } from '@/shared/http/http-client'
import { CalendarDate } from '@/shared/kernel/instant'
import { isErr, isOk } from '@/shared/kernel/result'
import { createCursorlessPage, createDateRange } from '@/domains/telemetry/domain/public'
import { HttpTelemetryGateway } from '@/domains/telemetry/infrastructure/http-telemetry.gateway'

import { server } from '../../../msw/server'
import { invalidDateRangeHandler } from '../../../msw/handlers/telemetry'

const ADMIN = 'http://localhost/bedrock-chat/admin'
const silentLogger = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} }
const gateway = new HttpTelemetryGateway(ADMIN, new HttpClient(), silentLogger)

const calendarDate = (value: string): CalendarDate => {
  const result = CalendarDate.fromIso(value)
  if (!isOk(result)) {
    throw new Error(`not a calendar date: ${value}`)
  }

  return result.value
}

const dateRange = createDateRange(calendarDate('2026-05-01'), calendarDate('2026-05-31'))

if (!isOk(dateRange)) {
  throw new Error('test date range must be valid')
}

const requestUrls: string[] = []

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
  server.events.on('request:start', ({ request }) => requestUrls.push(request.url))
})
afterEach(() => {
  server.resetHandlers()
  requestUrls.length = 0
})
afterAll(() => server.close())

describe('HttpTelemetryGateway', () => {
  it('maps summary token fields and derives totals', async () => {
    const result = await gateway.summary(AbortSignal.timeout(1_000))

    expect(isOk(result) && result.value).toEqual([
      { modelId: 'claude-3', tokens: { input: 120, output: 30, total: 150 }, turnCount: 4 },
    ])
  })

  it('encodes top-user limits and maps user totals', async () => {
    const result = await gateway.topUsers(5, AbortSignal.timeout(1_000))

    expect(requestUrls.at(-1)).toBe(`${ADMIN}/tokens/top-users?limit=5`)
    expect(isOk(result) && result.value[0]).toEqual({
      userId: 'alice@example.com',
      tokens: { input: 100, output: 50, total: 150 },
    })
  })

  it('encodes calendar dates as UTC boundaries with an exclusive end', async () => {
    await gateway.byDay(dateRange.value, AbortSignal.timeout(1_000))

    expect(requestUrls.at(-1)).toBe(
      `${ADMIN}/tokens/by-day?start=2026-05-01T00%3A00%3A00.000Z&end=2026-05-31T00%3A00%3A00.000Z`,
    )
  })

  it('maps per-user rows without inventing a total', async () => {
    const result = await gateway.byUser(
      { userId: 'alice@example.com', page: createCursorlessPage(50, 50, 0) },
      AbortSignal.timeout(1_000),
    )

    expect(requestUrls.at(-1)).toBe(
      `${ADMIN}/tokens/by-user?user_id=alice%40example.com&limit=50&offset=50`,
    )
    expect(isOk(result) && result.value[0]).toMatchObject({
      sessionId: 'session-1234567890',
      modelId: 'claude-3',
      tokens: { total: 50 },
      turnAt: { epochMilliseconds: 1778846400000 },
    })
  })

  it('preserves invalid_date_range from the server response', async () => {
    server.use(invalidDateRangeHandler)

    const result = await gateway.byDay(dateRange.value, AbortSignal.timeout(1_000))

    expect(isErr(result)).toBe(true)
    expect(isErr(result) && result.error).toMatchObject({
      code: 'http-error',
      status: 400,
      serverCode: 'invalid_date_range',
    })
  })

  it('reports malformed payloads as invalid responses', async () => {
    server.use(http.get('*/bedrock-chat/admin/tokens/summary', () => HttpResponse.json({ items: [{ model_id: 4 }] })))

    const result = await gateway.summary(AbortSignal.timeout(1_000))

    expect(isErr(result) && result.error.code).toBe('invalid-response')
  })
})