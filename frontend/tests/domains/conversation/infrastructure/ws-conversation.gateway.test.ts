import { describe, expect, it, vi } from 'vitest'

import { conversationId } from '@/shared/kernel/branded'
import { FRAME_OWNER, ServerFrameSchema, type ServerFrame, type ServerFrameSubscriber } from '@/shared/ws/message-bus'

import type { ConversationEvent } from '@/domains/conversation/domain/events'
import { WsConversationGateway } from '@/domains/conversation/infrastructure/ws-conversation.gateway'

// CT-1: the recorded wire fixtures, not hand-written objects, are what the mapping is proved against.
const recorded: Record<string, unknown> = import.meta.glob(
  '../../../msw/fixtures/ws/conversation_*.json',
  { eager: true, import: 'default' },
)

const frameTypeOf = (path: string): string => path.replace(/^.*\/([^/]+)\.json$/, '$1')

const parse = (fixture: unknown): ServerFrame => {
  const parsed = ServerFrameSchema.safeParse(fixture)

  if (!parsed.success) {
    throw new Error(`fixture does not match the server-frame schema: ${parsed.error.message}`)
  }

  return parsed.data
}

const createHarness = () => {
  const send = vi.fn<(frame: string) => 'sent' | 'dropped-closed'>(() => 'sent')
  let subscriber: ServerFrameSubscriber | undefined
  const subscribe = vi.fn((next: ServerFrameSubscriber) => {
    subscriber = next
    return () => {
      subscriber = undefined
    }
  })
  const gateway = new WsConversationGateway({ send }, { subscribe })
  const events: ConversationEvent[] = []
  gateway.onEvent((event) => events.push(event))

  return {
    events,
    gateway,
    sent: () => send.mock.calls.map((call) => JSON.parse(call[0]) as Record<string, unknown>),
    send,
    emit(frame: ServerFrame) {
      subscriber?.(frame)
    },
  }
}

describe('WsConversationGateway frame mapping', () => {
  it('has a recorded fixture for every conversation-owned frame (CT-1)', () => {
    const owned = Object.entries(FRAME_OWNER)
      .filter(([, owner]) => owner === 'conversation')
      .map(([type]) => type)
      .sort()

    expect(Object.keys(recorded).map(frameTypeOf).sort()).toEqual(owned)
    expect(owned).toHaveLength(9)
  })

  it.each(Object.entries(recorded).map(([path, fixture]) => [frameTypeOf(path), fixture] as const))(
    'maps the recorded %s frame',
    (_type, fixture) => {
      const harness = createHarness()

      harness.emit(parse(fixture))

      expect(harness.events).toHaveLength(1)
      expect(harness.events[0]?.kind).toBeTypeOf('string')
    },
  )

  it('ignores frames owned by another context', () => {
    const harness = createHarness()

    harness.emit({ type: 'pong', timestamp: '2026-08-25T12:00:00Z' })

    expect(harness.events).toEqual([])
  })

  // P8 / FR-CONV-009: only what the pending-turn rule reads crosses the context boundary.
  it('reduces a loaded message to its role and tool-call count', () => {
    const harness = createHarness()

    harness.emit({
      type: 'conversation_loaded',
      timestamp: '2026-08-25T12:00:00Z',
      conversation_id: 'c-1',
      conversation: {},
      messages: [
        {
          message_id: 'm-1',
          role: 'assistant',
          content: 'working',
          timestamp: '2026-08-25T12:00:00Z',
          tool_calls: [{}, {}],
          tool_results: [],
          metadata: {},
        },
      ],
    })

    expect(harness.events[0]).toEqual({
      kind: 'loaded',
      id: conversationId('c-1'),
      messages: [{ role: 'assistant', toolCallCount: 2 }],
    })
  })

  it('normalises an empty server title to null so FR-CONV-014 can supply the fallback', () => {
    const harness = createHarness()

    harness.emit({
      type: 'conversation_list',
      timestamp: '2026-08-25T12:00:00Z',
      conversations: [{ id: 'c-1', title: '  ', updated_at: '2026-08-25T12:00:00Z', message_count: 0 }],
    })

    expect(harness.events[0]).toMatchObject({ kind: 'listed', items: [{ title: null }] })
  })

  // An unparseable timestamp must not sort the thread to an arbitrary place in the list.
  it('floors an unparseable updated_at at the epoch', () => {
    const harness = createHarness()

    harness.emit({
      type: 'conversation_list',
      timestamp: '2026-08-25T12:00:00Z',
      conversations: [{ id: 'c-1', title: 'x', updated_at: 'not-a-date', message_count: 0 }],
    })

    const event = harness.events[0]
    expect(event?.kind === 'listed' && event.items[0]?.updatedAt.epochMilliseconds).toBe(0)
  })

  it('carries an absent conversation_id on an error frame through as null', () => {
    const harness = createHarness()

    harness.emit({
      type: 'conversation_error',
      timestamp: '2026-08-25T12:00:00Z',
      code: 'conversation_not_found',
      message: 'gone',
    })

    expect(harness.events[0]).toEqual({
      kind: 'error',
      code: 'conversation_not_found',
      message: 'gone',
      id: null,
    })
  })
})

describe('WsConversationGateway client frames', () => {
  // CONTRACT-001 §2.2.
  it('writes each mutation as its documented frame', () => {
    const harness = createHarness()
    const id = conversationId('c-1')

    harness.gateway.requestRoster({ limit: 50, offset: 0 })
    harness.gateway.create()
    harness.gateway.load(id)
    harness.gateway.rename(id, 'Job 42')
    harness.gateway.remove(id)
    harness.gateway.removeMany([id, conversationId('c-2')])
    harness.gateway.removeAll()

    expect(harness.sent()).toEqual([
      { type: 'conversation_list', limit: 50, offset: 0 },
      { type: 'conversation_new' },
      { type: 'conversation_load', conversation_id: 'c-1' },
      { type: 'conversation_rename', conversation_id: 'c-1', title: 'Job 42' },
      { type: 'conversation_delete', conversation_id: 'c-1' },
      { type: 'conversation_delete_bulk', conversation_ids: ['c-1', 'c-2'] },
      { type: 'conversation_delete_all' },
    ])
  })

  it('omits paging fields the caller did not supply', () => {
    const harness = createHarness()

    harness.gateway.requestRoster()

    expect(harness.sent()[0]).toEqual({ type: 'conversation_list' })
  })

  // ADR-012 / FR-CONV-017: a closed socket is reported, never queued behind a fallback transport.
  it('reports a closed socket back to the caller', () => {
    const harness = createHarness()
    harness.send.mockReturnValue('dropped-closed')

    expect(harness.gateway.remove(conversationId('c-1'))).toBe('dropped-closed')
  })
})
