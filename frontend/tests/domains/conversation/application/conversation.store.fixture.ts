import { jest } from '@jest/globals'

import type { ConversationId } from '@/shared/kernel/branded'
import { FixedClock, type Clock } from '@/shared/kernel/instant'
import { ScriptedConfirmationPort, type ScriptedAnswer } from '../../../shared/ports/scripted-confirmation-port'
import type { Logger } from '@/shared/logging/logger'
import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'
import type { ConnectionState, SendResult } from '@/shared/ws/socket-client'

import {
  ConversationStore,
  type ConversationStoreOptions,
} from '@/domains/conversation/application/conversation.store'
import type { ConnectionSource, ConversationGateway, PendingTurnScheduler } from '@/domains/conversation/application/ports'
import type { ConversationEvent } from '@/domains/conversation/domain/events'
import { at } from '../domain/conversation.fixture'

const openState: ConnectionState = { status: 'open', attempt: 0, nextRetryAt: null }
const closedState: ConnectionState = { status: 'closed', attempt: 1, nextRetryAt: null }

export const silentLogger: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
}

export type GatewayCall = readonly [method: string, ...args: unknown[]]

// A gateway that records what was written rather than one that also fakes replies: the store's
// job is to decide which frames leave, and the replies are driven separately through `emit`.
export const createFakeGateway = () => {
  const calls: GatewayCall[] = []
  let result: SendResult = 'sent'
  let publish: ((event: ConversationEvent) => void) | undefined

  const record = (method: string, ...args: unknown[]): SendResult => {
    calls.push([method, ...args])
    return result
  }

  const gateway: ConversationGateway = {
    requestRoster: (page) => record('requestRoster', page),
    create: () => record('create'),
    load: (id) => record('load', id),
    rename: (id, title) => record('rename', id, title),
    remove: (id) => record('remove', id),
    removeMany: (ids) => record('removeMany', [...ids]),
    removeAll: () => record('removeAll'),
    onEvent: (callback) => {
      publish = callback
      return () => {
        publish = undefined
      }
    },
  }

  return {
    gateway,
    calls,
    methods: () => calls.map(([method]) => method),
    setResult(next: SendResult) {
      result = next
    },
    emit(event: ConversationEvent) {
      publish?.(event)
    },
  }
}

// FR-CONV-009: ticks are driven by the test rather than by a timer, so twenty attempts take no
// wall-clock time and the interval the store asked for is assertable.
export const createFakeScheduler = () => {
  const intervals: number[] = []
  let tick: (() => void) | null = null

  const scheduler: PendingTurnScheduler = {
    every(intervalMs, callback) {
      intervals.push(intervalMs)
      tick = callback

      return () => {
        tick = null
      }
    },
  }

  return {
    scheduler,
    intervals,
    running: () => tick !== null,
    advance(times = 1) {
      for (let step = 0; step < times; step += 1) {
        tick?.()
      }
    },
  }
}

export const createFakeConnection = (initial: ConnectionState = openState) => {
  const listeners = new Set<(state: ConnectionState) => void>()
  let state = initial

  const source: ConnectionSource = {
    get state() {
      return state
    },
    onStateChange: (callback) => {
      listeners.add(callback)
      return () => listeners.delete(callback)
    },
  }

  return {
    source,
    set(next: ConnectionState) {
      state = next
      for (const listener of listeners) {
        listener(next)
      }
    },
    open: () => undefined,
  }
}

export type Harness = {
  readonly store: ConversationStore
  readonly gateway: ReturnType<typeof createFakeGateway>
  readonly connection: ReturnType<typeof createFakeConnection>
  readonly scheduler: ReturnType<typeof createFakeScheduler>
  readonly confirmations: ScriptedConfirmationPort
  readonly notifications: RecordingNotificationPort
  readonly logger: Logger
}

export type HarnessOptions = {
  readonly answers?: readonly ScriptedAnswer[]
  readonly connected?: boolean
  readonly persistenceEnabled?: boolean
  readonly authenticated?: boolean
  readonly clock?: Clock
  readonly overrides?: Partial<ConversationStoreOptions>
}

export const createHarness = ({
  answers = [],
  connected = true,
  persistenceEnabled = true,
  authenticated = true,
  clock = new FixedClock(at(0)),
  overrides = {},
}: HarnessOptions = {}): Harness => {
  const gateway = createFakeGateway()
  const connection = createFakeConnection(connected ? openState : closedState)
  const scheduler = createFakeScheduler()
  const confirmations = new ScriptedConfirmationPort(answers)
  const notifications = new RecordingNotificationPort()
  const logger = { ...silentLogger, warn: jest.fn() }

  const store = new ConversationStore({
    gateway: gateway.gateway,
    connection: connection.source,
    confirmations,
    notifications,
    scheduler: scheduler.scheduler,
    clock,
    logger,
    persistenceEnabled,
    ...overrides,
  })

  if (authenticated) {
    store.setAuthenticated(true)
    gateway.calls.length = 0
  }

  return { store, gateway, connection, scheduler, confirmations, notifications, logger }
}

export const idOf = (name: string): ConversationId => name as ConversationId

export { closedState, openState }
