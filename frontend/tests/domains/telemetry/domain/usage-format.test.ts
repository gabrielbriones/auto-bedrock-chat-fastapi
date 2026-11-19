import { describe, expect, it } from '@jest/globals'

import { formatEstimatedCost, formatTokenCount } from '@/domains/telemetry/domain/public'

describe('formatTokenCount', () => {
  it.each([
    [0, '0'],
    [340, '340'],
    [1234, '1.2k'],
    [12_000, '12k'],
    [999_950, '1M'],
    [3_400_000, '3.4M'],
  ])('shows %d compactly as %s', (value, expected) => {
    expect(formatTokenCount(value)).toBe(expected)
  })

  it('groups the exact count', () => {
    expect(formatTokenCount(1234567, 'exact')).toBe('1,234,567')
  })
})

describe('formatEstimatedCost', () => {
  it('keeps four decimals below one unit and two above', () => {
    expect(formatEstimatedCost(0.0123, 'USD')).toBe('$0.0123')
    expect(formatEstimatedCost(0.5, 'USD')).toBe('$0.50')
    expect(formatEstimatedCost(12.3456, 'USD')).toBe('$12.35')
  })

  it('never rounds a positive estimate down to zero', () => {
    expect(formatEstimatedCost(0.00001, 'USD')).toBe('<$0.0001')
  })

  it('uses the currency the server priced in, defaulting to USD', () => {
    expect(formatEstimatedCost(0.0123, 'EUR')).toBe('€0.0123')
    expect(formatEstimatedCost(0.0123, null)).toBe('$0.0123')
  })

  it('still shows an amount whose currency is not an ISO code', () => {
    expect(formatEstimatedCost(0.0123, 'credits')).toBe('0.0123 credits')
  })
})
