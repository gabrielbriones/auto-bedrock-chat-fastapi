import { beforeEach, describe, expect, it, jest } from '@jest/globals'

import { FixedClock, Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'
import type { ConnectionState } from '@/shared/ws/socket-client'

import { ChatSessionStore } from '@/domains/messaging/application/chat-session.store'
import type { MessagingEvent } from '@/domains/messaging/application/ports'

const fixedInstant = () => {
  const instant = Instant.fromIso('2026-08-28T10:00:00Z')
  if (!isOk(instant)) {
    throw new Error('unparseable fixture instant')
  }

  return instant.value
}

const state = (
  status: ConnectionState['status'],
  attempt = 0,
): ConnectionState => ({ status, attempt, nextRetryAt: null })

const createHarness = (initial: ConnectionState = state('open')) => {
  const sendChat = jest.fn<(text: string) => 'sent' | 'dropped-closed'>(() => 'sent')
  const logger = { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() }

  let emitEvent: ((event: MessagingEvent) => void) | undefined
  let emitState: ((next: ConnectionState) => void) | undefined
  let current = initial

  const store = new ChatSessionStore({
    clock: new FixedClock(fixedInstant()),
    logger,
    newId: (() => {
      let count = 0
      return () => {
        count += 1
        return `turn-${count}`
      }
    })(),
    gateway: {
      sendChat,
      onEvent: (callback) => {
        emitEvent = callback
        return () => {
          emitEvent = undefined
        }
      },
    },
    connection: {
      get state() {
        return current
      },
      onStateChange: (callback) => {
        emitState = callback
        return () => {
          emitState = undefined
        }
      },
    },
  })

  return {
    logger,
    sendChat,
    store,
    emit(event: MessagingEvent) {
      emitEvent?.(event)
    },
    changeConnection(next: ConnectionState) {
      current = next
      emitState?.(next)
    },
  }
}

const answered = (text: string): MessagingEvent => ({
  kind: 'answered',
  text,
  messageId: 'm-1',
  conversationId: 'c-1',
  toolCalls: [],
  toolResults: [],
  citations: [],
  truncated: false,
  configuredModel: { id: 'model-1', name: 'Model 1' },
})

describe('ChatSessionStore', () => {
  it('publishes the configured model reported by the latest answer', () => {
    const { store, emit } = createHarness()

    emit(answered('response'))

    expect(store.getSnapshot().configuredModel).toEqual({ id: 'model-1', name: 'Model 1' })
  })

  describe('connection state (FR-MSG-002)', () => {
    it('follows connect, drop, reconnect and intentional close', () => {
      const { store, changeConnection } = createHarness(state('connecting'))

      expect(store.getSnapshot().connection).toEqual({ kind: 'connecting' })
      expect(store.getSnapshot().canSend).toBe(false)

      changeConnection(state('open'))
      expect(store.getSnapshot().connection).toEqual({ kind: 'connected' })

      changeConnection(state('reconnecting', 2))
      expect(store.getSnapshot().connection).toEqual({ kind: 'disconnected' })

      changeConnection(state('open'))
      expect(store.getSnapshot().connection).toEqual({ kind: 'connected' })

      changeConnection(state('closed'))
      expect(store.getSnapshot().connection).toEqual({ kind: 'disconnected' })
    })

    it('holds a session id only while the connection that issued it is open (I1)', () => {
      const { store, emit, changeConnection } = createHarness()

      emit({ kind: 'session-established', sessionId: 's-1' })
      expect(store.getSnapshot().sessionId).toBe('s-1')

      changeConnection(state('reconnecting', 1))
      expect(store.getSnapshot().sessionId).toBeNull()
    })

    it('notifies subscribers on every change', () => {
      const { store, changeConnection } = createHarness(state('connecting'))
      const listener = jest.fn()
      store.subscribe(listener)

      changeConnection(state('open'))

      expect(listener).toHaveBeenCalledTimes(1)
      expect(store.getSnapshot()).not.toBe(undefined)
    })
  })

  describe('sending (FR-MSG-013)', () => {
    let harness: ReturnType<typeof createHarness>

    beforeEach(() => {
      harness = createHarness()
    })

    it('echoes the message optimistically before the server replies', () => {
      expect(harness.store.send('analyse job 42')).toBe('sent')

      const { transcript, awaitingResponse } = harness.store.getSnapshot()
      expect(transcript).toHaveLength(1)
      expect(transcript[0]?.message).toMatchObject({ role: 'user', raw: 'analyse job 42' })
      expect(transcript[0]?.pending).toBe(true)
      expect(awaitingResponse).toBe(true)
    })

    it('moves the pending turn to streaming and shows the cumulative snapshot when a typing frame arrives', () => {
      harness.store.send('analyse job 42')

      harness.emit({ kind: 'typing', text: 'IPC is' })

      const { transcript, awaitingResponse } = harness.store.getSnapshot()
      expect(transcript[0]?.pending).toBe(true)
      expect(transcript[1]?.message).toMatchObject({ role: 'assistant', raw: 'IPC is' })
      expect(awaitingResponse).toBe(true)
    })

    it('trims trailing whitespace and refuses an empty message without a network call', () => {
      expect(harness.store.send('   ')).toBe('empty')
      expect(harness.sendChat).not.toHaveBeenCalled()

      harness.store.send('  hello  ')
      expect(harness.sendChat).toHaveBeenCalledWith('hello')
    })

    it('refuses a second send while a turn is unresolved (FR-MSG-009)', () => {
      harness.store.send('first')

      expect(harness.store.send('second')).toBe('refused')
      expect(harness.sendChat).toHaveBeenCalledTimes(1)
    })

    it('refuses to send while the socket is not open', () => {
      const closed = createHarness(state('closed'))

      expect(closed.store.send('hello')).toBe('refused')
      expect(closed.sendChat).not.toHaveBeenCalled()
    })

    it('abandons the echo when the socket drops the frame rather than queueing it', () => {
      harness.sendChat.mockReturnValue('dropped-closed')

      expect(harness.store.send('hello')).toBe('dropped-closed')
      expect(harness.store.getSnapshot().awaitingResponse).toBe(false)
      expect(harness.store.getSnapshot().transcript).toHaveLength(1)
    })
  })

  describe('answering (FR-MSG-007)', () => {
    it('renders ai_response as an assistant message without duplicating the request', () => {
      const { store, emit } = createHarness()
      store.send('analyse job 42')

      emit(answered('here you go'))

      const { transcript, awaitingResponse } = store.getSnapshot()
      expect(transcript.map((entry) => [entry.message.role, entry.message.raw])).toEqual([
        ['user', 'analyse job 42'],
        ['assistant', 'here you go'],
      ])
      expect(transcript[0]?.pending).toBe(false)
      expect(awaitingResponse).toBe(false)
    })

    it('attaches the server message id to the assistant message', () => {
      const { store, emit } = createHarness()
      store.send('hi')

      emit(answered('hello'))

      expect(store.getSnapshot().transcript[1]?.message.id).toBe('m-1')
    })

    it('ignores a duplicate answer for a turn that already resolved', () => {
      const { store, emit, logger } = createHarness()
      store.send('hi')

      emit(answered('hello'))
      emit(answered('hello again'))

      expect(store.getSnapshot().transcript).toHaveLength(3)
      expect(store.getSnapshot().transcript[2]?.transient).toBe(true)
      expect(logger.warn).not.toHaveBeenCalled()
    })

    it('fails the pending turn on an error frame and unlocks input', () => {
      const { store, emit } = createHarness()
      store.send('hi')

      emit({ kind: 'failed', text: 'model unavailable' })

      expect(store.getSnapshot().awaitingResponse).toBe(false)
      expect(store.getSnapshot().transcript[1]?.message.raw).toBe('model unavailable')
    })

    it('shows an unattributable error as a transient notice (ADR-013)', () => {
      const { store, emit } = createHarness()

      emit({ kind: 'failed', text: 'session expired' })

      expect(store.getSnapshot().transcript).toEqual([
        expect.objectContaining({ transient: true }),
      ])
    })
  })

  describe('loading conversation history (FR-CONV-003)', () => {
    it('replaces the current transcript with the selected conversation', () => {
      const { store, emit } = createHarness()
      store.send('new conversation draft')

      emit({
        kind: 'history-loaded',
        conversationId: 'c-2',
        messages: [
          {
            id: 'm-2',
            role: 'user',
            text: 'Show the Stream triad results',
            at: fixedInstant(),
            activity: null,
          },
          {
            id: 'm-3',
            role: 'assistant',
            text: 'The measured bandwidth was 118 GB/s.',
            at: fixedInstant(),
            activity: null,
          },
        ],
      })

      expect(
        store.getSnapshot().transcript.map((entry) => [entry.message.role, entry.message.raw]),
      ).toEqual([
        ['user', 'Show the Stream triad results'],
        ['assistant', 'The measured bandwidth was 118 GB/s.'],
      ])
      expect(store.getSnapshot().awaitingResponse).toBe(false)
    })

    it('shows the tool activity behind a persisted answer', () => {
      const { store, emit } = createHarness()
      const activity = {
        toolCalls: [{ id: 'call-1', name: 'get_job', arguments: {} }],
        toolResults: [],
        citations: [],
        truncated: false,
      }

      emit({
        kind: 'history-loaded',
        conversationId: 'c-2',
        messages: [{ id: 'm-3', role: 'assistant', text: 'Done.', at: fixedInstant(), activity }],
      })

      expect(store.getSnapshot().transcript[0]?.activity).toBe(activity)
    })
  })

  it('abandons the pending turn when the socket closes (FR-MSG-024)', () => {
    const { store, changeConnection } = createHarness()
    store.send('hi')

    changeConnection(state('reconnecting', 1))

    expect(store.getSnapshot().awaitingResponse).toBe(false)
    expect(store.getSnapshot().canSend).toBe(false)
  })

  // FR-MSG-005: staleness reaches the store as the recycled connection the heartbeat forced.
  it('recycles a stale turn into a visible interruption that keeps its partial text', () => {
    const { store, emit, changeConnection } = createHarness()
    store.send('hi')
    emit({ kind: 'typing', text: 'IPC is' })

    changeConnection(state('reconnecting', 1))

    const transcript = store.getSnapshot().transcript
    expect(transcript.map((entry) => entry.interrupted)).toEqual([false, true])
    expect(transcript[1]?.message.raw).toBe('IPC is')
    expect(store.getSnapshot().awaitingResponse).toBe(false)

    changeConnection(state('open', 0))

    expect(store.getSnapshot().canSend).toBe(true)
    expect(store.send('hi')).toBe('sent')
  })

  it('records the shape of the first typing frame once, for P1 (ADR-008)', () => {
    const { emit, logger } = createHarness()

    emit({ kind: 'typing', text: 'Hello wor' })
    emit({ kind: 'typing', text: 'Hello world' })

    expect(logger.info).toHaveBeenCalledTimes(1)
    expect(logger.info).toHaveBeenCalledWith('ws_typing_observed', { length: 9 })
  })

  it('stops listening once disposed', () => {
    const { store, emit } = createHarness()
    const listener = jest.fn()
    store.subscribe(listener)

    store.dispose()
    emit({ kind: 'session-established', sessionId: 's-1' })

    expect(listener).not.toHaveBeenCalled()
  })
})
