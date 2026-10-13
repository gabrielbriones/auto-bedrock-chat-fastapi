import type { MessageBus, ServerFrame } from '@/shared/ws/message-bus'
import type { SendResult, SocketClient, Unsubscribe } from '@/shared/ws/socket-client'

import type {
  ConfigUpdatedEvent,
  ConfigurationGateway,
} from '@/domains/model-config/application/ports'
import {
  isOverrideKey,
  type OverrideKey,
  type OverrideValue,
} from '@/domains/model-config/domain/override-key'

type SocketWriter = Pick<SocketClient, 'send'>
type FrameSource = Pick<MessageBus, 'subscribe'>

const isOverrideValue = (value: unknown): value is OverrideValue =>
  typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'

const toOverrides = (
  values: Readonly<Record<string, unknown>>,
): Partial<Record<OverrideKey, OverrideValue>> => {
  const overrides: Partial<Record<OverrideKey, OverrideValue>> = {}

  for (const [key, value] of Object.entries(values)) {
    if (isOverrideKey(key) && isOverrideValue(value)) {
      overrides[key] = value
    }
  }

  return overrides
}

const toUpdatedEvent = (frame: ServerFrame): ConfigUpdatedEvent | null => {
  if (frame.type !== 'config_updated') {
    return null
  }

  return {
    activeOverrides: toOverrides(frame.active_overrides),
    appliedOverrides: toOverrides(frame.applied_overrides),
    rejectedOverrides: frame.rejected_overrides.filter(
      (reason): reason is string => typeof reason === 'string' && reason.length > 0,
    ),
  }
}

export class WsModelConfigGateway implements ConfigurationGateway {
  readonly #frames: FrameSource
  readonly #socket: SocketWriter

  constructor(socket: SocketWriter, frames: FrameSource) {
    this.#socket = socket
    this.#frames = frames
  }

  update(overrides: Readonly<Partial<Record<OverrideKey, OverrideValue>>>): SendResult {
    return this.#write({ type: 'config_update', config_overrides: overrides, override_mode: 'session' })
  }

  reset(): SendResult {
    return this.#write({ type: 'config_reset' })
  }

  onUpdated(callback: (event: ConfigUpdatedEvent) => void): Unsubscribe {
    return this.#frames.subscribe((frame) => {
      const event = toUpdatedEvent(frame)

      if (event !== null) {
        callback(event)
      }
    })
  }

  #write(frame: object): SendResult {
    return this.#socket.send(JSON.stringify(frame))
  }
}
