import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals'

import { TimeoutPollScheduler } from '@/domains/knowledge/infrastructure/timeout-poll.scheduler'

describe('TimeoutPollScheduler', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('fires the callback once after the delay', () => {
    const callback = jest.fn()

    new TimeoutPollScheduler().schedule(callback, 1_500)
    jest.advanceTimersByTime(1_499)
    expect(callback).not.toHaveBeenCalled()

    jest.advanceTimersByTime(1)
    jest.advanceTimersByTime(5_000)
    expect(callback).toHaveBeenCalledTimes(1)
  })

  it('does not fire once cancelled', () => {
    const callback = jest.fn()

    const cancel = new TimeoutPollScheduler().schedule(callback, 1_500)
    cancel()
    jest.advanceTimersByTime(5_000)

    expect(callback).not.toHaveBeenCalled()
  })
})
