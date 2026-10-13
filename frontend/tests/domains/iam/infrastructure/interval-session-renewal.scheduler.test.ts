import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals'

import { IntervalSessionRenewalScheduler } from '@/domains/iam/infrastructure/interval-session-renewal.scheduler'

describe('IntervalSessionRenewalScheduler', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('ticks on every interval and stops once cancelled', () => {
    const tick = jest.fn()
    const cancel = new IntervalSessionRenewalScheduler().every(1_000, tick)

    jest.advanceTimersByTime(999)
    expect(tick).not.toHaveBeenCalled()

    jest.advanceTimersByTime(2_001)
    expect(tick).toHaveBeenCalledTimes(3)

    cancel()
    jest.advanceTimersByTime(5_000)
    expect(tick).toHaveBeenCalledTimes(3)
  })
})
