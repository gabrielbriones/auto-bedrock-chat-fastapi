import type {
  NotificationKind,
  NotificationPort,
  NotifyOptions,
} from '@/shared/ports/notification-port'

export type RecordedNotification = {
  readonly kind: NotificationKind
  readonly message: string
  readonly options?: NotifyOptions
}

// STD-002 §4: assert on what the app told the user, not on sonner's internals.
export class RecordingNotificationPort implements NotificationPort {
  readonly notifications: RecordedNotification[] = []

  success(message: string, options?: NotifyOptions): void {
    this.#record('success', message, options)
  }

  error(message: string, options?: NotifyOptions): void {
    this.#record('error', message, options)
  }

  info(message: string, options?: NotifyOptions): void {
    this.#record('info', message, options)
  }

  warning(message: string, options?: NotifyOptions): void {
    this.#record('warning', message, options)
  }

  messages(): readonly string[] {
    return this.notifications.map((notification) => notification.message)
  }

  #record(kind: NotificationKind, message: string, options?: NotifyOptions): void {
    this.notifications.push({ kind, message, ...(options !== undefined ? { options } : {}) })
  }
}
