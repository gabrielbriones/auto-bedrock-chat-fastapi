import type { Cancel, PollScheduler } from '@/domains/knowledge/application/ports'

// The one adapter allowed to reach for `setTimeout`; the store's polling policy is exercised
// through the port instead, so a test drives a whole ingestion run without waiting on the clock.
export class TimeoutPollScheduler implements PollScheduler {
  schedule(callback: () => void, delayMs: number): Cancel {
    const handle = setTimeout(callback, delayMs)

    return () => {
      clearTimeout(handle)
    }
  }
}
