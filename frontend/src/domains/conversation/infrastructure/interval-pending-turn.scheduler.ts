import type { Cancel, PendingTurnScheduler } from '@/domains/conversation/application/ports'

// The one adapter allowed to reach for `setInterval`; FR-CONV-009's policy is exercised through the
// port instead, so a test drives 20 attempts without waiting two minutes.
export class IntervalPendingTurnScheduler implements PendingTurnScheduler {
  every(intervalMs: number, tick: () => void): Cancel {
    const handle = setInterval(tick, intervalMs)

    return () => {
      clearInterval(handle)
    }
  }
}
