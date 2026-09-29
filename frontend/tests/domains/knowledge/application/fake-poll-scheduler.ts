import type { Cancel, PollScheduler } from '@/domains/knowledge/application/ports'

// STD-002 §4: the store's polling policy is driven by hand — each `flush()` fires whatever the
// store scheduled, so a whole ingestion run plays out without the clock.
export class FakePollScheduler implements PollScheduler {
  readonly delays: number[] = []
  #pending: (() => void)[] = []

  schedule(callback: () => void, delayMs: number): Cancel {
    this.delays.push(delayMs)
    this.#pending.push(callback)

    return () => {
      this.#pending = this.#pending.filter((candidate) => candidate !== callback)
    }
  }

  get pendingCount(): number {
    return this.#pending.length
  }

  async flush(): Promise<void> {
    const callbacks = this.#pending
    this.#pending = []
    for (const callback of callbacks) {
      callback()
    }
    // Let the poll's own async work (status fetch + follow-up) settle.
    await new Promise<void>((resolve) => setTimeout(resolve, 0))
  }
}
