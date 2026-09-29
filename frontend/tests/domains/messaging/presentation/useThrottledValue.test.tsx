import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, jest } from '@jest/globals'

import { useThrottledValue } from '@/domains/messaging/presentation/useThrottledValue'

const INTERVAL_MS = 1000

afterEach(() => {
  jest.useRealTimers()
})

describe('useThrottledValue', () => {
  it('emits the leading change at once and no more than one per interval', () => {
    jest.useFakeTimers()

    const { result, rerender } = renderHook(({ value }) => useThrottledValue(value, INTERVAL_MS), {
      initialProps: { value: 'first' },
    })

    act(() => {
      jest.advanceTimersByTime(0)
    })
    expect(result.current).toBe('first')

    rerender({ value: 'second' })
    act(() => {
      jest.advanceTimersByTime(INTERVAL_MS - 1)
    })
    expect(result.current).toBe('first')

    act(() => {
      jest.advanceTimersByTime(1)
    })
    expect(result.current).toBe('second')
  })

  it('collapses a burst to the last value in it', () => {
    jest.useFakeTimers()

    const { result, rerender } = renderHook(({ value }) => useThrottledValue(value, INTERVAL_MS), {
      initialProps: { value: 'a' },
    })

    act(() => {
      jest.advanceTimersByTime(0)
    })

    for (const value of ['b', 'c', 'd']) {
      rerender({ value })
      act(() => {
        jest.advanceTimersByTime(10)
      })
    }

    expect(result.current).toBe('a')

    act(() => {
      jest.advanceTimersByTime(INTERVAL_MS)
    })

    expect(result.current).toBe('d')
  })
})
