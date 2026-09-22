import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

import { SonnerNotificationPort } from '@/app/adapters/sonner-notification-port'
import { Instant, type Clock } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

class MovableClock implements Clock {
  #epochMilliseconds = 0

  now(): Instant {
    const instant = Instant.fromEpochMilliseconds(this.#epochMilliseconds)

    if (!isOk(instant)) {
      throw new Error('unreachable')
    }

    return instant.value
  }

  advance(milliseconds: number): void {
    this.#epochMilliseconds += milliseconds
  }
}

const spies = {
  success: vi.spyOn(toast, 'success'),
  error: vi.spyOn(toast, 'error'),
  info: vi.spyOn(toast, 'info'),
  warning: vi.spyOn(toast, 'warning'),
}

beforeEach(() => {
  for (const spy of Object.values(spies)) {
    spy.mockReset().mockReturnValue('id')
  }
})

describe('SonnerNotificationPort', () => {
  it('dismisses after the legacy 4 000 ms by default', () => {
    new SonnerNotificationPort(new MovableClock()).success('Saved')

    expect(spies.success).toHaveBeenCalledWith('Saved', { duration: 4_000 })
  })

  it('collapses the same message repeated inside the dedupe window into one toast', () => {
    const clock = new MovableClock()
    const notifications = new SonnerNotificationPort(clock)

    notifications.error('Connection lost')
    clock.advance(400)
    notifications.error('Connection lost')

    expect(spies.error).toHaveBeenCalledTimes(1)
  })

  it('shows the message again once the window has passed', () => {
    const clock = new MovableClock()
    const notifications = new SonnerNotificationPort(clock)

    notifications.error('Connection lost')
    clock.advance(1_500)
    notifications.error('Connection lost')

    expect(spies.error).toHaveBeenCalledTimes(2)
  })

  it('does not collapse different messages or different kinds', () => {
    const notifications = new SonnerNotificationPort(new MovableClock())

    notifications.info('One')
    notifications.info('Two')
    notifications.warning('One')

    expect(spies.info).toHaveBeenCalledTimes(2)
    expect(spies.warning).toHaveBeenCalledTimes(1)
  })
})
