import { describe, expect, it } from 'vitest'

import { CalendarDate } from '@/shared/kernel/instant'
import { isErr, isOk } from '@/shared/kernel/result'
import {
  createCursorlessPage,
  createDateRange,
  createTokenCount,
} from '@/domains/telemetry/domain/public'

const date = (value: string) => {
  const result = CalendarDate.fromIso(value)
  if (!isOk(result)) {
    throw new Error(`invalid test date: ${value}`)
  }

  return result.value
}

describe('telemetry value objects', () => {
  it('derives total tokens from input and output', () => {
    expect(createTokenCount(120, 30)).toEqual({ input: 120, output: 30, total: 150 })
  })

  it('accepts a strictly increasing date range', () => {
    const result = createDateRange(date('2026-05-01'), date('2026-05-31'))

    if (!isOk(result)) {
      throw new Error('expected a valid date range')
    }

    expect(result.value.start.toIso()).toBe('2026-05-01')
  })

  it.each([
    ['same day', '2026-05-10', '2026-05-10'],
    ['inverted', '2026-05-10', '2026-05-01'],
  ])('rejects an %s date range with a stated reason', (_name, start, end) => {
    const result = createDateRange(date(start), date(end))

    if (!isErr(result)) {
      throw new Error('expected an invalid date range')
    }

    expect(result.error.message).toBe('End date must be after start date.')
  })

  it('derives cursorless navigation from the returned row count', () => {
    expect(createCursorlessPage(0, 50, 50)).toMatchObject({ hasNext: true, hasPrev: false })
    expect(createCursorlessPage(50, 50, 37)).toMatchObject({ hasNext: false, hasPrev: true })
    expect(createCursorlessPage(50, 50, 0)).toMatchObject({ hasNext: false, hasPrev: true })
  })
})