import { afterEach, describe, expect, it, vi } from 'vitest'

import { FakeConnectivityPort } from '../ports/fake-connectivity-port'
import { FixedClock, Instant } from '@/shared/kernel/instant'
import { FakeChatSocket, type FakeChatSocketStep } from './fake-chat-socket'
import {
  SocketClient,
  type SocketClientOptions,
  type SocketFactory,
} from '@/shared/ws/socket-client'

const PONG_FRAME = '{"type":"pong","timestamp":"2026-08-25T12:00:00Z"}'

const fixedClock = (): FixedClock => {
  const instant = Instant.fromEpochMilliseconds(1_000)
  if ('error' in instant) {
    throw new Error('failed to create fixed instant')
  }

  return new FixedClock(instant.value)
}

const createSocketFactory = (
  steps: readonly FakeChatSocketStep[] = [{ type: 'open' }],
): { readonly factory: SocketFactory; readonly sockets: FakeChatSocket[] } => {
  const sockets: FakeChatSocket[] = []

  return {
    sockets,
    factory: () => {
      const socket = new FakeChatSocket(steps)
      sockets.push(socket)
      return socket
    },
  }
}

const createClient = (
  options: Partial<SocketClientOptions> & Pick<SocketClientOptions, 'socketFactory'>,
): SocketClient =>
  new SocketClient({
    clock: fixedClock(),
    connectivity: new FakeConnectivityPort(),
    random: () => 0.5,
    url: 'ws://example.test/chat',
    ...options,
  })

afterEach(() => {
  vi.useRealTimers()
})

describe('SocketClient', () => {
  it('connects once and only sends after the socket opens', () => {
    vi.useFakeTimers()
    const socketFactory = createSocketFactory()
    const client = createClient({ socketFactory: socketFactory.factory })

    client.connect()

    expect(socketFactory.sockets).toHaveLength(1)
    expect(client.state.status).toBe('connecting')
    expect(client.send('before-open')).toBe('dropped-closed')

    socketFactory.sockets[0]?.playNext()

    expect(client.send('after-open')).toBe('sent')
    expect(socketFactory.sockets[0]?.sent).toEqual(['after-open'])
    client.close()
  })

  it('delivers raw frames to subscribers until they unsubscribe', () => {
    vi.useFakeTimers()
    const socketFactory = createSocketFactory([
      { type: 'open' },
      { type: 'frame', data: PONG_FRAME },
      { type: 'frame', data: PONG_FRAME },
    ])
    const client = createClient({ socketFactory: socketFactory.factory })
    const frames: unknown[] = []

    client.connect()
    const unsubscribe = client.onFrame((frame) => frames.push(frame))
    const socket = socketFactory.sockets[0]
    socket?.playNext()
    socket?.playNext()
    unsubscribe()
    socket?.playNext()

    expect(frames).toEqual([PONG_FRAME])
    client.close()
  })

  it('reports a send that fails mid-flight as dropped', () => {
    vi.useFakeTimers()
    const socketFactory = createSocketFactory([{ type: 'open' }, { type: 'close-during-send' }])
    const client = createClient({ socketFactory: socketFactory.factory })

    client.connect()
    socketFactory.sockets[0]?.playNext()
    socketFactory.sockets[0]?.playNext()

    expect(client.send('in-flight')).toBe('dropped-closed')
    expect(client.state.status).toBe('reconnecting')
    client.close()
  })

  it('cancels a pending reconnect after an intentional close', () => {
    vi.useFakeTimers()
    const socketFactory = createSocketFactory()
    const client = createClient({ socketFactory: socketFactory.factory })

    client.connect()
    socketFactory.sockets[0]?.playNext()
    socketFactory.sockets[0]?.close()

    expect(client.state.status).toBe('reconnecting')
    client.close()
    vi.advanceTimersByTime(30_000)

    expect(socketFactory.sockets).toHaveLength(1)
    expect(client.state).toMatchObject({ status: 'closed', attempt: 0, nextRetryAt: null })
  })

  it('retries when the handshake never completes', () => {
    vi.useFakeTimers()
    const socketFactory = createSocketFactory()
    const client = createClient({ openTimeoutMs: 5_000, socketFactory: socketFactory.factory })

    client.connect()
    vi.advanceTimersByTime(5_000)

    expect(client.state).toMatchObject({ status: 'reconnecting', attempt: 1 })
    expect(socketFactory.sockets).toHaveLength(1)

    vi.advanceTimersByTime(1_000)

    expect(socketFactory.sockets).toHaveLength(2)
    client.close()
  })

  it('suspends a pending retry offline and reconnects immediately when online', () => {
    vi.useFakeTimers()
    const connectivity = new FakeConnectivityPort()
    const socketFactory = createSocketFactory()
    const client = createClient({ connectivity, socketFactory: socketFactory.factory })

    client.connect()
    socketFactory.sockets[0]?.playNext()
    socketFactory.sockets[0]?.close()
    connectivity.set('offline')
    vi.advanceTimersByTime(30_000)

    expect(socketFactory.sockets).toHaveLength(1)
    expect(client.state).toMatchObject({ status: 'reconnecting', attempt: 1, nextRetryAt: null })

    connectivity.set('online')

    expect(socketFactory.sockets).toHaveLength(2)
    expect(client.state).toMatchObject({ status: 'reconnecting', attempt: 1, nextRetryAt: null })
    client.close()
  })

  it('waits to create the first socket until connectivity returns', () => {
    vi.useFakeTimers()
    const connectivity = new FakeConnectivityPort('offline')
    const socketFactory = createSocketFactory()
    const client = createClient({ connectivity, socketFactory: socketFactory.factory })

    client.connect()

    expect(socketFactory.sockets).toHaveLength(0)
    expect(client.state).toMatchObject({ status: 'reconnecting', attempt: 0, nextRetryAt: null })

    connectivity.set('online')

    expect(socketFactory.sockets).toHaveLength(1)
    expect(client.state.status).toBe('connecting')
    client.close()
  })

  it('publishes every status transition to state listeners', () => {
    vi.useFakeTimers()
    const socketFactory = createSocketFactory()
    const client = createClient({ socketFactory: socketFactory.factory })
    const statuses: string[] = []

    const unsubscribe = client.onStateChange((state) => statuses.push(state.status))
    client.connect()
    socketFactory.sockets[0]?.playNext()
    socketFactory.sockets[0]?.close()
    unsubscribe()
    client.close()

    expect(statuses).toEqual(['connecting', 'open', 'reconnecting'])
  })

  it('stops reacting to connectivity once disposed', () => {
    vi.useFakeTimers()
    const connectivity = new FakeConnectivityPort('offline')
    const socketFactory = createSocketFactory()
    const client = createClient({ connectivity, socketFactory: socketFactory.factory })

    client.connect()
    client.dispose()
    connectivity.set('online')

    expect(socketFactory.sockets).toHaveLength(0)
    expect(client.state.status).toBe('closed')
  })

  it('stops automatic retries at the cap and allows a manual reconnect', () => {
    vi.useFakeTimers()
    const connectivity = new FakeConnectivityPort()
    const socketFactory = createSocketFactory()
    const client = createClient({
      connectivity,
      random: () => 0,
      reconnectPolicy: {
        baseDelayMs: 1,
        maxDelayMs: 1,
        maxAttempts: 1,
        minJitterMultiplier: 1,
        maxJitterMultiplier: 1,
      },
      socketFactory: socketFactory.factory,
    })

    client.connect()
    socketFactory.sockets[0]?.close()
    vi.advanceTimersByTime(1)
    socketFactory.sockets[1]?.close()
    vi.runAllTimers()

    expect(socketFactory.sockets).toHaveLength(2)
    expect(client.state).toMatchObject({ status: 'closed', attempt: 1, nextRetryAt: null })

    connectivity.set('offline')
    connectivity.set('online')

    expect(socketFactory.sockets).toHaveLength(2)

    client.connect()

    expect(socketFactory.sockets).toHaveLength(3)
    expect(client.state).toMatchObject({ status: 'connecting', attempt: 0, nextRetryAt: null })
    client.close()
  })

  describe('heartbeat and staleness (FR-MSG-005)', () => {
    it('sends a ping on every heartbeat interval while the socket stays open', () => {
      vi.useFakeTimers()
      const socketFactory = createSocketFactory()
      const client = createClient({
        heartbeatIntervalMs: 30_000,
        staleTimeoutMs: 90_000,
        socketFactory: socketFactory.factory,
      })

      client.connect()
      socketFactory.sockets[0]?.playNext()
      vi.advanceTimersByTime(30_000)

      expect(socketFactory.sockets[0]?.sent).toEqual(['{"type":"ping"}'])

      client.close()
    })

    it('resets staleness whenever any frame arrives, not only on a pong', () => {
      vi.useFakeTimers()
      const socketFactory = createSocketFactory([
        { type: 'open' },
        { type: 'frame', data: PONG_FRAME },
      ])
      const client = createClient({
        heartbeatIntervalMs: 30_000,
        staleTimeoutMs: 90_000,
        socketFactory: socketFactory.factory,
      })

      client.connect()
      socketFactory.sockets[0]?.playNext()
      vi.advanceTimersByTime(30_000)
      socketFactory.sockets[0]?.playNext()
      vi.advanceTimersByTime(60_000)

      // Two heartbeat intervals elapsed since the connection opened, but a frame reset the
      // staleness count after the first, so the connection is still alive, not recycled.
      expect(client.state.status).toBe('open')
      expect(socketFactory.sockets).toHaveLength(1)

      client.close()
    })

    it('recycles a connection that receives no frame within the stale timeout', () => {
      vi.useFakeTimers()
      const socketFactory = createSocketFactory()
      const client = createClient({
        heartbeatIntervalMs: 30_000,
        staleTimeoutMs: 90_000,
        socketFactory: socketFactory.factory,
      })
      const statuses: string[] = []

      client.onStateChange((state) => statuses.push(state.status))
      client.connect()
      socketFactory.sockets[0]?.playNext()
      // Three missed heartbeat intervals (90s) with no inbound frame: stale.
      vi.advanceTimersByTime(90_000)

      expect(client.state.status).toBe('reconnecting')
      expect(statuses).toContain('reconnecting')

      client.close()
    })

    it('stops the heartbeat once the socket is closed intentionally', () => {
      vi.useFakeTimers()
      const socketFactory = createSocketFactory()
      const client = createClient({
        heartbeatIntervalMs: 30_000,
        staleTimeoutMs: 90_000,
        socketFactory: socketFactory.factory,
      })

      client.connect()
      socketFactory.sockets[0]?.playNext()
      client.close()
      vi.advanceTimersByTime(90_000)

      expect(socketFactory.sockets[0]?.sent).toEqual([])
      expect(socketFactory.sockets).toHaveLength(1)
    })
  })
})
