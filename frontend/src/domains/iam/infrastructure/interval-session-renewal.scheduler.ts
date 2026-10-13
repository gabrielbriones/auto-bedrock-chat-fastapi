import type { Cancel, SessionRenewalScheduler } from '@/domains/iam/application/ports'

export class IntervalSessionRenewalScheduler implements SessionRenewalScheduler {
  every(intervalMs: number, tick: () => void): Cancel {
    const handle = setInterval(tick, intervalMs)

    return () => {
      clearInterval(handle)
    }
  }
}
