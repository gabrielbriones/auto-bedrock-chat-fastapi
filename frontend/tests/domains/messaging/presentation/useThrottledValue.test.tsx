import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useThrottledValue } from '@/domains/messaging/presentation/useThrottledValue'

const INTERVAL_MS = 1000

afterEach(() => {
  vi.useRealTimers()
})

describe('useThrottledValue', () => {
  it('emits the leading change at once and no more than one per interval', () => {
    vi.useFakeTimers()

    const { result, rerender } = renderHook(({ value }) => useThrottledValue(value, INTERVAL_MS), {
      initialProps: { value: 'first' },
    })

    act(() => {
      vi.advanceTimersByTime(0)
    })
    expect(result.current).toBe('first')

    rerender({ value: 'second' })
    act(() => {
      vi.advanceTimersByTime(INTERVAL_MS - 1)
    })
    expect(result.current).toBe('first')

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current).toBe('second')
  })

  it('collapses a burst to the last value in it', () => {
    vi.useFakeTimers()

    const { result, rerender } = renderHook(({ value }) => useThrottledValue(value, INTERVAL_MS), {
      initialProps: { value: 'a' },
    })

    act(() => {
      vi.advanceTimersByTime(0)
    })

    for (const value of ['b', 'c', 'd']) {
      rerender({ value })
      act(() => {
        vi.advanceTimersByTime(10)
      })
    }

    expect(result.current).toBe('a')

    act(() => {
      vi.advanceTimersByTime(INTERVAL_MS)
    })

    expect(result.current).toBe('d')
  })
})
