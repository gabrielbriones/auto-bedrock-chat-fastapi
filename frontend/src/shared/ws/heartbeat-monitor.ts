import type { SocketTimer } from '@/shared/ws/socket-client'

export type HeartbeatMonitorOptions = {
  readonly heartbeatIntervalMs: number
  readonly onPing: () => void
  readonly onStale: () => void
  readonly staleTimeoutMs: number
  readonly timer: SocketTimer
}

// FR-MSG-005 / T-080: a ping keeps the connection alive; if no frame of any kind arrives within
// `staleTimeoutMs`, the socket looks identical to a dead one to a naive client. Ticks are counted
// against the timer itself, not the wall clock, so recycling is exact and deterministic under
// fake timers — a `Clock` fixed in tests would never observe elapsed time at all.
export class HeartbeatMonitor {
  #handle: ReturnType<typeof setTimeout> | undefined
  readonly #options: HeartbeatMonitorOptions
  #ticks = 0

  constructor(options: HeartbeatMonitorOptions) {
    this.#options = options
  }

  /** Call once the connection is open. Restarts the tick count from zero. */
  start(): void {
    this.#ticks = 0
    this.#schedule()
  }

  /** Call whenever a frame of any kind arrives — resets the staleness count. */
  markFrameReceived(): void {
    this.#ticks = 0
  }

  /** Call whenever the monitored connection closes, drops, or is replaced. */
  stop(): void {
    if (this.#handle === undefined) {
      return
    }

    this.#options.timer.clearTimeout(this.#handle)
    this.#handle = undefined
  }

  #schedule(): void {
    this.#handle = this.#options.timer.setTimeout(() => {
      this.#ticks += 1
      const staleAfterTicks = Math.max(
        1,
        Math.ceil(this.#options.staleTimeoutMs / this.#options.heartbeatIntervalMs),
      )

      if (this.#ticks >= staleAfterTicks) {
        this.#options.onStale()
        return
      }

      this.#options.onPing()
      this.#schedule()
    }, this.#options.heartbeatIntervalMs)
  }
}
