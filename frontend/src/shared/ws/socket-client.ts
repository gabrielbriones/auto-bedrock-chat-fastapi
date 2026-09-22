import type { ConnectivityPort } from '@/shared/ports/connectivity-port'
import { Instant, type Clock } from '@/shared/kernel/instant'

import { HeartbeatMonitor } from '@/shared/ws/heartbeat-monitor'
import {
  DEFAULT_RECONNECT_POLICY,
  reconnectDelayMs,
  type ReconnectPolicy,
} from '@/shared/ws/reconnect-policy'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'reconnecting'

export type ConnectionState = {
  readonly status: ConnectionStatus
  readonly attempt: number
  readonly nextRetryAt: Instant | null
}

export type SendResult = 'sent' | 'dropped-closed'

export type Unsubscribe = () => void

export interface SocketConnection {
  onclose: ((event: CloseEvent) => void) | null
  onmessage: ((event: MessageEvent<unknown>) => void) | null
  onopen: ((event: Event) => void) | null
  close(): void
  send(message: string): void
}

export type SocketFactory = (url: string) => SocketConnection

export interface SocketTimer {
  clearTimeout(handle: ReturnType<typeof setTimeout>): void
  setTimeout(callback: () => void, delayMs: number): ReturnType<typeof setTimeout>
}

export type SocketClientOptions = {
  readonly clock: Clock
  readonly connectivity: ConnectivityPort
  /** FR-MSG-005: how often a `ping` is sent while the socket is open. */
  readonly heartbeatIntervalMs?: number
  readonly openTimeoutMs?: number
  readonly random?: () => number
  readonly reconnectPolicy?: ReconnectPolicy
  readonly socketFactory?: SocketFactory
  /** FR-MSG-005: no frame of any kind within this window recycles the connection. */
  readonly staleTimeoutMs?: number
  readonly timer?: SocketTimer
  readonly url: string
}

const DEFAULT_OPEN_TIMEOUT_MS = 10_000
const DEFAULT_HEARTBEAT_INTERVAL_MS = 30_000
const DEFAULT_STALE_TIMEOUT_MS = 90_000
const PING_FRAME = '{"type":"ping"}'

const browserSocketFactory: SocketFactory = (url) => new WebSocket(url)

const browserTimer: SocketTimer = {
  clearTimeout(handle) {
    clearTimeout(handle)
  },
  setTimeout(callback, delayMs) {
    return setTimeout(callback, delayMs)
  },
}

// Transport-only owner of the application WebSocket (ADR-004). Frames remain raw strings here;
// message-bus.ts will own parsing and validation.
export class SocketClient {
  #attempt = 0
  #connection: SocketConnection | undefined
  readonly #connectivity: ConnectivityPort
  readonly #clock: Clock
  readonly #heartbeat: HeartbeatMonitor
  #intentionalClose = false
  #onConnectivityChange: Unsubscribe | undefined
  #openTimer: ReturnType<typeof setTimeout> | undefined
  readonly #openTimeoutMs: number
  readonly #policy: ReconnectPolicy
  readonly #random: () => number
  #retryTimer: ReturnType<typeof setTimeout> | undefined
  readonly #socketFactory: SocketFactory
  #state: ConnectionState = { status: 'idle', attempt: 0, nextRetryAt: null }
  readonly #stateListeners = new Set<(state: ConnectionState) => void>()
  #shouldConnect = false
  readonly #timer: SocketTimer
  readonly #url: string

  constructor(options: SocketClientOptions) {
    this.#clock = options.clock
    this.#connectivity = options.connectivity
    this.#openTimeoutMs = options.openTimeoutMs ?? DEFAULT_OPEN_TIMEOUT_MS
    this.#policy = options.reconnectPolicy ?? DEFAULT_RECONNECT_POLICY
    this.#random = options.random ?? Math.random
    this.#socketFactory = options.socketFactory ?? browserSocketFactory
    this.#timer = options.timer ?? browserTimer
    this.#url = options.url
    this.#heartbeat = new HeartbeatMonitor({
      heartbeatIntervalMs: options.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS,
      staleTimeoutMs: options.staleTimeoutMs ?? DEFAULT_STALE_TIMEOUT_MS,
      timer: this.#timer,
      onPing: () => {
        try {
          this.#connection?.send(PING_FRAME)
        } catch {
          // A send failure here is not this monitor's job to resolve; `send()` and the next
          // staleness check (or the native close event) handle recovery.
        }
      },
      onStale: () => {
        this.#connection?.close()
      },
    })
  }

  get state(): ConnectionState {
    return this.#state
  }

  close(): void {
    this.#intentionalClose = true
    this.#shouldConnect = false
    this.#attempt = 0
    this.#clearRetryTimer()
    this.#clearOpenTimer()
    this.#heartbeat.stop()

    const connection = this.#connection
    this.#connection = undefined
    if (connection !== undefined) {
      connection.onclose = null
      connection.onmessage = null
      connection.onopen = null
      connection.close()
    }

    this.#setState({ status: 'closed', attempt: 0, nextRetryAt: null })
  }

  connect(): void {
    this.#intentionalClose = false
    this.#shouldConnect = true

    // Deferred so that constructing a client is inert: the composition root builds one before
    // there is any DOM to subscribe to (STD-002 §4).
    this.#onConnectivityChange ??= this.#connectivity.subscribe((status) => {
      this.#handleConnectivityChange(status)
    })

    if (this.#state.status === 'closed') {
      this.#attempt = 0
    }

    if (this.#connection !== undefined || this.#retryTimer !== undefined) {
      return
    }

    if (this.#connectivity.status() === 'offline') {
      // Waiting for connectivity reports as `reconnecting` here and after a drop alike.
      this.#setState({ status: 'reconnecting', attempt: this.#attempt, nextRetryAt: null })
      return
    }

    this.#openConnection()
  }

  dispose(): void {
    this.close()
    this.#onConnectivityChange?.()
    this.#onConnectivityChange = undefined
  }

  onFrame(listener: (frame: unknown) => void): Unsubscribe {
    this.#frameListeners.add(listener)

    return () => {
      this.#frameListeners.delete(listener)
    }
  }

  onStateChange(listener: (state: ConnectionState) => void): Unsubscribe {
    this.#stateListeners.add(listener)

    return () => {
      this.#stateListeners.delete(listener)
    }
  }

  send(frame: string): SendResult {
    if (this.#state.status !== 'open' || this.#connection === undefined) {
      return 'dropped-closed'
    }

    try {
      this.#connection.send(frame)
      return 'sent'
    } catch {
      return 'dropped-closed'
    }
  }

  readonly #frameListeners = new Set<(frame: unknown) => void>()

  #clearRetryTimer(): void {
    if (this.#retryTimer === undefined) {
      return
    }

    this.#timer.clearTimeout(this.#retryTimer)
    this.#retryTimer = undefined
  }

  #clearOpenTimer(): void {
    if (this.#openTimer === undefined) {
      return
    }

    this.#timer.clearTimeout(this.#openTimer)
    this.#openTimer = undefined
  }

  // A handshake that never completes emits no close event, so it needs its own deadline.
  #abandonConnection(connection: SocketConnection): void {
    if (this.#connection !== connection) {
      return
    }

    this.#connection = undefined
    this.#heartbeat.stop()
    connection.onclose = null
    connection.onmessage = null
    connection.onopen = null

    try {
      connection.close()
    } catch {
      // A socket that never opened may reject close(); the reconnect below is what matters.
    }

    this.#scheduleReconnect()
  }

  #handleClose(connection: SocketConnection): void {
    if (this.#connection !== connection) {
      return
    }

    this.#clearOpenTimer()
    this.#heartbeat.stop()
    this.#connection = undefined
    this.#scheduleReconnect()
  }

  #handleConnectivityChange(status: 'online' | 'offline'): void {
    if (status === 'offline') {
      this.#clearRetryTimer()

      if (this.#shouldConnect && this.#connection === undefined && !this.#intentionalClose) {
        this.#setState({ status: 'reconnecting', attempt: this.#attempt, nextRetryAt: null })
      }

      return
    }

    if (
      this.#shouldConnect &&
      !this.#intentionalClose &&
      this.#connection === undefined &&
      this.#retryTimer === undefined
    ) {
      this.#openConnection()
    }
  }

  #openConnection(): void {
    if (!this.#shouldConnect || this.#intentionalClose || this.#connectivity.status() === 'offline') {
      return
    }

    this.#setState({
      status: this.#attempt === 0 ? 'connecting' : 'reconnecting',
      attempt: this.#attempt,
      nextRetryAt: null,
    })

    let connection: SocketConnection
    try {
      connection = this.#socketFactory(this.#url)
    } catch {
      this.#scheduleReconnect()
      return
    }

    this.#connection = connection
    connection.onopen = () => {
      if (this.#connection !== connection) {
        return
      }

      this.#clearOpenTimer()
      this.#attempt = 0
      this.#heartbeat.start()
      this.#setState({ status: 'open', attempt: 0, nextRetryAt: null })
    }
    connection.onmessage = (event) => {
      if (this.#connection !== connection) {
        return
      }

      this.#heartbeat.markFrameReceived()

      for (const listener of this.#frameListeners) {
        listener(event.data)
      }
    }
    connection.onclose = () => {
      this.#handleClose(connection)
    }

    this.#openTimer = this.#timer.setTimeout(() => {
      this.#openTimer = undefined
      this.#abandonConnection(connection)
    }, this.#openTimeoutMs)
  }

  #scheduleReconnect(): void {
    if (!this.#shouldConnect || this.#intentionalClose) {
      return
    }

    const attempt = this.#attempt + 1
    const delayMs = reconnectDelayMs(attempt, this.#random(), this.#policy)
    if (delayMs === undefined) {
      this.#shouldConnect = false
      this.#setState({ status: 'closed', attempt: this.#attempt, nextRetryAt: null })
      return
    }

    this.#attempt = attempt
    if (this.#connectivity.status() === 'offline') {
      this.#setState({ status: 'reconnecting', attempt, nextRetryAt: null })
      return
    }

    const nextRetryAt = this.#instantAfter(delayMs)
    this.#setState({ status: 'reconnecting', attempt, nextRetryAt })
    this.#retryTimer = this.#timer.setTimeout(() => {
      this.#retryTimer = undefined
      this.#openConnection()
    }, delayMs)
  }

  #instantAfter(delayMs: number): Instant {
    const now = this.#clock.now()
    const instant = Instant.fromEpochMilliseconds(now.epochMilliseconds + delayMs)

    if ('error' in instant) {
      throw new Error('reconnect delay produced an invalid instant')
    }

    return instant.value
  }

  #setState(state: ConnectionState): void {
    this.#state = state

    for (const listener of this.#stateListeners) {
      listener(state)
    }
  }
}