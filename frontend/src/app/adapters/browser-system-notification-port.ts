import type {
  SystemNotification,
  SystemNotificationPermission,
  SystemNotificationPort,
} from '@/shared/ports/system-notification-port'

// The slice of the `Notification` global this adapter relies on, so a test can supply a fake.
export type NotificationApi = {
  readonly permission: NotificationPermission
  requestPermission(callback?: (permission: NotificationPermission) => void): Promise<NotificationPermission> | undefined
  new (title: string, options?: NotificationOptions): Notification
}

export type BrowserSystemNotificationPortOptions = {
  readonly api?: NotificationApi | undefined
  readonly focusWindow?: () => void
  readonly iconUrl?: () => string | undefined
}

const browserApi = (): NotificationApi | undefined =>
  typeof Notification === 'undefined' ? undefined : (Notification as unknown as NotificationApi)

const focusBrowserWindow = (): void => {
  window.focus()
}

const documentIcon = (): string | undefined =>
  typeof document === 'undefined'
    ? undefined
    : (document.querySelector<HTMLLinkElement>('link[rel~="icon"]')?.href ?? undefined)

const normalise = (permission: unknown): SystemNotificationPermission =>
  permission === 'granted' || permission === 'denied' ? permission : 'default'

// FR-MSG-032. Every browser quirk stays here: a missing API, Safari's callback-only
// `requestPermission`, and Android Chrome's constructor that throws outside a service worker.
export class BrowserSystemNotificationPort implements SystemNotificationPort {
  readonly #api: NotificationApi | undefined
  readonly #focusWindow: () => void
  readonly #iconUrl: () => string | undefined

  constructor(options: BrowserSystemNotificationPortOptions = {}) {
    this.#api = 'api' in options ? options.api : browserApi()
    this.#focusWindow = options.focusWindow ?? focusBrowserWindow
    this.#iconUrl = options.iconUrl ?? documentIcon
  }

  permission(): SystemNotificationPermission {
    return this.#api === undefined ? 'unsupported' : normalise(this.#api.permission)
  }

  requestPermission(): Promise<SystemNotificationPermission> {
    const api = this.#api
    if (api === undefined) {
      return Promise.resolve('unsupported')
    }

    return new Promise((resolve) => {
      const settle = (permission: unknown) => {
        resolve(normalise(permission))
      }

      try {
        api.requestPermission(settle)?.then(settle, () => {
          settle(api.permission)
        })
      } catch {
        settle(api.permission)
      }
    })
  }

  show({ title, body, tag, onClick }: SystemNotification): void {
    const api = this.#api
    if (api === undefined || this.permission() !== 'granted') {
      return
    }

    const icon = this.#iconUrl()

    try {
      const notification = new api(title, { body, tag, ...(icon === undefined ? {} : { icon }) })
      notification.onclick = () => {
        this.#focusWindow()
        notification.close()
        onClick()
      }
    } catch {
      // No OS notification on this platform; the in-app transcript is unaffected.
    }
  }
}
