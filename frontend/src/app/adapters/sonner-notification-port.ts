import { toast } from 'sonner'

import type {
  NotificationKind,
  NotificationPort,
  NotifyOptions,
} from '@/shared/ports/notification-port'
import { SystemClock, type Clock } from '@/shared/kernel/instant'

// FR-SHELL-007: legacy parity on dismissal, and a 1 s window in which the same message from a
// retry loop or a burst of frames collapses into one toast.
const DEFAULT_DURATION_MS = 4_000
const DEDUPE_WINDOW_MS = 1_000

export class SonnerNotificationPort implements NotificationPort {
  readonly #clock: Clock
  readonly #lastShownAt = new Map<string, number>()

  constructor(clock: Clock = new SystemClock()) {
    this.#clock = clock
  }

  success(message: string, options?: NotifyOptions): void {
    this.#show('success', message, options)
  }

  error(message: string, options?: NotifyOptions): void {
    this.#show('error', message, options)
  }

  info(message: string, options?: NotifyOptions): void {
    this.#show('info', message, options)
  }

  warning(message: string, options?: NotifyOptions): void {
    this.#show('warning', message, options)
  }

  #show(kind: NotificationKind, message: string, options?: NotifyOptions): void {
    const key = `${kind}:${message}`
    const now = this.#clock.now().epochMilliseconds
    const lastShownAt = this.#lastShownAt.get(key)

    if (lastShownAt !== undefined && now - lastShownAt < DEDUPE_WINDOW_MS) {
      return
    }

    this.#lastShownAt.set(key, now)

    toast[kind](message, {
      duration: options?.durationMs ?? DEFAULT_DURATION_MS,
      ...(options?.description !== undefined ? { description: options.description } : {}),
    })
  }
}
