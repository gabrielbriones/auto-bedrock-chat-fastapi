import type { PageAttentionPort } from '@/shared/ports/page-attention-port'
import type { PreferenceStoragePort } from '@/shared/ports/preference-storage-port'
import type {
  SystemNotification,
  SystemNotificationPermission,
  SystemNotificationPort,
} from '@/shared/ports/system-notification-port'

// STD-002 §4: drive the browser's permission prompt and record what would have been shown.
export class FakeSystemNotificationPort implements SystemNotificationPort {
  current: SystemNotificationPermission
  /** What the next permission prompt resolves to. */
  answer: SystemNotificationPermission = 'granted'
  requests = 0
  readonly shown: SystemNotification[] = []

  constructor(initial: SystemNotificationPermission = 'granted') {
    this.current = initial
  }

  permission(): SystemNotificationPermission {
    return this.current
  }

  requestPermission(): Promise<SystemNotificationPermission> {
    this.requests += 1
    this.current = this.answer
    return Promise.resolve(this.current)
  }

  show(notification: SystemNotification): void {
    if (this.current === 'granted') {
      this.shown.push(notification)
    }
  }
}

export class FakePageAttentionPort implements PageAttentionPort {
  #attended: boolean
  readonly #listeners = new Set<(attended: boolean) => void>()

  constructor(attended = true) {
    this.#attended = attended
  }

  isAttended(): boolean {
    return this.#attended
  }

  subscribe(listener: (attended: boolean) => void): () => void {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  set(attended: boolean): void {
    this.#attended = attended

    for (const listener of this.#listeners) {
      listener(attended)
    }
  }
}

export class InMemoryPreferenceStorage implements PreferenceStoragePort {
  readonly values = new Map<string, string>()

  read(key: string): string | null {
    return this.values.get(key) ?? null
  }

  write(key: string, value: string): void {
    this.values.set(key, value)
  }
}
