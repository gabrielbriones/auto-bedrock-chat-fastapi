import type { NotificationPort } from '@/shared/ports/notification-port'
import type { ConnectionState, Unsubscribe } from '@/shared/ws/socket-client'

import type {
  ConfigUpdatedEvent,
  ConfigurationGateway,
} from '@/domains/model-config/application/ports'
import { clampMaxTokens } from '@/domains/model-config/domain/clamp'
import { effective, type ConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { resolveEffectiveModel } from '@/domains/model-config/domain/effective-model'
import type { OverrideKey, OverrideValue } from '@/domains/model-config/domain/override-key'
import { MODEL_CONFIG_COPY } from '@/shared/copy/model-config'

export type ModelConfigSnapshot = {
  readonly profile: ConfigurationProfile
  readonly pendingKeys: ReadonlySet<OverrideKey>
  readonly resetPending: boolean
  readonly rejectionReasons: readonly string[]
}

export type ModelConfigConnection = {
  readonly state: ConnectionState
  onStateChange(callback: (state: ConnectionState) => void): Unsubscribe
}

export type ModelConfigStoreOptions = {
  readonly profile: ConfigurationProfile
  readonly gateway: ConfigurationGateway
  readonly connection: ModelConfigConnection
  readonly notifications: NotificationPort
}

type PendingOperation = { readonly kind: 'update'; readonly keys: readonly OverrideKey[] } | { readonly kind: 'reset' }

export class ModelConfigStore {
  readonly #gateway: ConfigurationGateway
  readonly #listeners = new Set<() => void>()
  readonly #notifications: NotificationPort
  #pending: readonly PendingOperation[] = []
  #reconnectingWithPending = false
  #snapshot: ModelConfigSnapshot
  #subscriptions: readonly Unsubscribe[]

  constructor(options: ModelConfigStoreOptions) {
    this.#gateway = options.gateway
    this.#notifications = options.notifications
    this.#snapshot = {
      profile: options.profile,
      pendingKeys: new Set(),
      resetPending: false,
      rejectionReasons: [],
    }
    this.#subscriptions = [
      this.#gateway.onUpdated((event) => this.#applyConfirmation(event)),
      options.connection.onStateChange((state) => this.#handleConnectionState(state)),
    ]
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    return () => this.#listeners.delete(listener)
  }

  getSnapshot = (): ModelConfigSnapshot => this.#snapshot

  commit(key: OverrideKey, value: OverrideValue): void {
    if (this.#gateway.update({ [key]: value }) === 'dropped-closed') {
      return
    }

    this.#pending = [...this.#pending, { kind: 'update', keys: [key] }]
    this.#rebuildPending([])
  }

  reset(): void {
    if (this.#gateway.reset() === 'dropped-closed') {
      return
    }

    this.#pending = [...this.#pending, { kind: 'reset' }]
    this.#rebuildPending([])
  }

  dismissRejections(): void {
    if (this.#snapshot.rejectionReasons.length === 0) {
      return
    }

    this.#snapshot = { ...this.#snapshot, rejectionReasons: [] }
    this.#emit()
  }

  dispose(): void {
    for (const unsubscribe of this.#subscriptions) {
      unsubscribe()
    }
    this.#listeners.clear()
  }

  #applyConfirmation(event: ConfigUpdatedEvent): void {
    const profile = { ...this.#snapshot.profile, overrides: event.activeOverrides }
    this.#snapshot = {
      ...this.#snapshot,
      profile,
      rejectionReasons: event.rejectedOverrides,
    }

    if (event.rejectedOverrides.length > 0) {
      this.#notifications.warning(MODEL_CONFIG_COPY.rejection.title, {
        description: event.rejectedOverrides.join('; '),
      })
    }

    if (this.#reconnectingWithPending) {
      this.#pending = []
      this.#reconnectingWithPending = false
    } else {
      this.#pending = this.#pending.slice(1)
    }

    const maxTokens = effective(profile, 'max_tokens')
    if (typeof maxTokens === 'number') {
      const clamped = clampMaxTokens(maxTokens, resolveEffectiveModel(profile))
      if (clamped !== maxTokens && this.#gateway.update({ max_tokens: clamped }) === 'sent') {
        this.#pending = [...this.#pending, { kind: 'update', keys: ['max_tokens'] }]
        this.#notifications.info(MODEL_CONFIG_COPY.maxTokensClamped(clamped))
      }
    }

    this.#rebuildPending(event.rejectedOverrides)
  }

  #handleConnectionState(state: ConnectionState): void {
    if (state.status !== 'open' && this.#pending.length > 0) {
      this.#reconnectingWithPending = true
    }
  }

  #rebuildPending(rejectionReasons: readonly string[]): void {
    const pendingKeys = new Set<OverrideKey>()
    let resetPending = false

    for (const operation of this.#pending) {
      if (operation.kind === 'reset') {
        resetPending = true
      } else {
        for (const key of operation.keys) {
          pendingKeys.add(key)
        }
      }
    }

    this.#snapshot = { ...this.#snapshot, pendingKeys, resetPending, rejectionReasons }
    this.#emit()
  }

  #emit(): void {
    for (const listener of this.#listeners) {
      listener()
    }
  }
}
