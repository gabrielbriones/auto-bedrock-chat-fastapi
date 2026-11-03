import type { ConversationId } from '@/shared/kernel/branded'
import { REPLY_NOTIFICATION_COPY } from '@/shared/copy/reply-notifications'
import type { PageAttentionPort } from '@/shared/ports/page-attention-port'
import type { PreferenceStoragePort } from '@/shared/ports/preference-storage-port'
import type {
  SystemNotificationPermission,
  SystemNotificationPort,
} from '@/shared/ports/system-notification-port'
import type { Unsubscribe } from '@/shared/ws/socket-client'

import type { ChatSessionStore, SettledReply } from '@/domains/messaging/application/chat-session.store'
import { replyPreview } from '@/app/notifications/reply-preview'

export const REPLY_NOTIFICATIONS_ENABLED_KEY = 'chat.replyNotifications.enabled'
export const REPLY_NOTIFICATIONS_PROMPTED_KEY = 'chat.replyNotifications.prompted'

export type ReplyNotificationSnapshot = {
  /** False when the browser has no Notifications API: the control is not offered at all. */
  readonly supported: boolean
  readonly permission: SystemNotificationPermission
  /** The user's saved choice; on until they turn it off. */
  readonly enabled: boolean
  /** Notifications will actually be shown: the user wants them and the browser allows them. */
  readonly active: boolean
  /** Replies that landed while the user was away, for the tab-title indicator. */
  readonly unread: number
}

/** Where a reply belongs, resolved by the composition root from the conversation roster. */
export type ReplyContext = {
  readonly conversationId: ConversationId | null
  readonly title: string
}

export type ReplyNotificationStoreOptions = {
  readonly chatSession: Pick<ChatSessionStore, 'onReplySettled'>
  readonly system: SystemNotificationPort
  readonly attention: PageAttentionPort
  readonly storage: PreferenceStoragePort
  readonly describe: (reply: SettledReply) => ReplyContext
}

// FR-MSG-032. Decides whether a settled reply becomes an OS notification. It never asks for
// permission on load: only on the user's first send, or when they switch the control on.
export class ReplyNotificationStore {
  readonly #activateListeners = new Set<(id: ConversationId | null) => void>()
  #enabled: boolean
  readonly #listeners = new Set<() => void>()
  readonly #options: ReplyNotificationStoreOptions
  #permission: SystemNotificationPermission
  // Held in memory too, so a storage failure cannot turn "ask once" into "ask on every send".
  #prompted: boolean
  #snapshot: ReplyNotificationSnapshot
  #subscriptions: readonly Unsubscribe[]
  #unread = 0

  constructor(options: ReplyNotificationStoreOptions) {
    this.#options = options
    this.#enabled = options.storage.read(REPLY_NOTIFICATIONS_ENABLED_KEY) !== 'false'
    this.#prompted = options.storage.read(REPLY_NOTIFICATIONS_PROMPTED_KEY) === 'true'
    this.#permission = options.system.permission()
    this.#snapshot = this.#build()

    this.#subscriptions = [
      options.chatSession.onReplySettled((reply) => {
        this.#notify(reply)
      }),
      options.attention.subscribe((attended) => {
        this.#observeAttention(attended)
      }),
    ]
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  getSnapshot = (): ReplyNotificationSnapshot => this.#snapshot

  /** Fired when the user clicks a notification, with the conversation that received the reply. */
  onActivate(listener: (id: ConversationId | null) => void): Unsubscribe {
    this.#activateListeners.add(listener)

    return () => {
      this.#activateListeners.delete(listener)
    }
  }

  async enable(): Promise<SystemNotificationPermission> {
    this.#setEnabled(true)

    if (this.#permission === 'default') {
      await this.#requestPermission()
    }

    return this.#permission
  }

  disable(): void {
    this.#setEnabled(false)
  }

  /**
   * Call from the handler of a send the user made (composer or preset click), never from an
   * automatic one such as a deep-link autosend: the first such send in this browser is the user
   * gesture the permission prompt piggybacks on, once.
   */
  userSent(): void {
    if (this.#prompted || !this.#enabled || this.#permission !== 'default') {
      return
    }

    void this.#requestPermission()
  }

  dispose(): void {
    for (const unsubscribe of this.#subscriptions) {
      unsubscribe()
    }

    this.#subscriptions = []
    this.#listeners.clear()
    this.#activateListeners.clear()
  }

  #notify(reply: SettledReply): void {
    this.#syncPermission()

    if (!this.#snapshot.active || this.#options.attention.isAttended()) {
      return
    }

    const { conversationId, title } = this.#options.describe(reply)
    const preview = replyPreview(reply.text)
    const body = reply.outcome === 'failed'
      ? REPLY_NOTIFICATION_COPY.failed(preview)
      : preview || REPLY_NOTIFICATION_COPY.emptyReply

    this.#unread += 1
    this.#emit()

    this.#options.system.show({
      title,
      body,
      tag: `reply:${conversationId ?? 'new'}`,
      onClick: () => {
        this.#activate(conversationId)
      },
    })
  }

  #observeAttention(attended: boolean): void {
    this.#syncPermission()

    if (attended && this.#unread > 0) {
      this.#unread = 0
      this.#emit()
    }
  }

  #activate(id: ConversationId | null): void {
    if (this.#unread > 0) {
      this.#unread = 0
      this.#emit()
    }

    for (const listener of this.#activateListeners) {
      listener(id)
    }
  }

  async #requestPermission(): Promise<void> {
    this.#prompted = true
    this.#options.storage.write(REPLY_NOTIFICATIONS_PROMPTED_KEY, 'true')
    this.#permission = await this.#options.system.requestPermission()
    this.#emit()
  }

  #setEnabled(enabled: boolean): void {
    this.#enabled = enabled
    this.#options.storage.write(REPLY_NOTIFICATIONS_ENABLED_KEY, String(enabled))
    this.#permission = this.#options.system.permission()
    this.#emit()
  }

  // The user can change the site permission in browser settings at any time, with no event.
  #syncPermission(): void {
    const permission = this.#options.system.permission()
    if (permission !== this.#permission) {
      this.#permission = permission
      this.#emit()
    }
  }

  #build(): ReplyNotificationSnapshot {
    return {
      supported: this.#permission !== 'unsupported',
      permission: this.#permission,
      enabled: this.#enabled,
      active: this.#enabled && this.#permission === 'granted',
      unread: this.#unread,
    }
  }

  #emit(): void {
    this.#snapshot = this.#build()

    for (const listener of this.#listeners) {
      listener()
    }
  }
}
