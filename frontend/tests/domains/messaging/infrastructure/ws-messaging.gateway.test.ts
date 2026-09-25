import { describe, expect, it, jest } from '@jest/globals'

import type { ServerFrame, ServerFrameSubscriber } from '@/shared/ws/message-bus'

import type { MessagingEvent } from '@/domains/messaging/application/ports'
import { WsMessagingGateway } from '@/domains/messaging/infrastructure/ws-messaging.gateway'

const TIMESTAMP = '2026-08-28T10:00:00Z'

const createHarness = () => {
  const send = jest.fn<(frame: string) => 'sent' | 'dropped-closed'>(() => 'sent')
  let subscriber: ServerFrameSubscriber | undefined
  const subscribe = jest.fn((next: ServerFrameSubscriber) => {
    subscriber = next
    return () => {
      subscriber = undefined
    }
  })
  const gateway = new WsMessagingGateway({ send }, { subscribe })
  const events: MessagingEvent[] = []
  const unsubscribe = gateway.onEvent((event) => events.push(event))

  return {
    events,
    gateway,
    send,
    unsubscribe,
    emit(frame: ServerFrame) {
      subscriber?.(frame)
    },
  }
}

const answer = (overrides: Partial<Extract<ServerFrame, { type: 'ai_response' }>> = {}) =>
  ({
    type: 'ai_response',
    timestamp: TIMESTAMP,
    message: 'here you go',
    message_id: 'm-1',
    tool_calls: [],
    tool_results: [],
    metadata: {
      model_id: 'claude',
      model_name: 'Claude',
      tool_call_rounds: 0,
      total_tool_calls: 0,
      preprocessing_applied: false,
    },
    conversation_id: 'c-1',
    ...overrides,
  }) as Extract<ServerFrame, { type: 'ai_response' }>

describe('WsMessagingGateway', () => {
  it('serialises a chat frame per CONTRACT-001 §2.2 and never adds a token', () => {
    const { gateway, send } = createHarness()

    expect(gateway.sendChat('analyse job 42')).toBe('sent')
    expect(JSON.parse(send.mock.calls[0]?.[0] ?? '')).toEqual({
      type: 'chat',
      message: 'analyse job 42',
    })
  })

  it('reports a closed socket back to the caller rather than queueing', () => {
    const { gateway, send } = createHarness()
    send.mockReturnValue('dropped-closed')

    expect(gateway.sendChat('hello')).toBe('dropped-closed')
  })

  it('maps connection_established to the session id', () => {
    const { emit, events } = createHarness()

    emit({ type: 'connection_established', timestamp: TIMESTAMP, session_id: 's-1', message: 'hi' })

    expect(events).toEqual([{ kind: 'session-established', sessionId: 's-1' }])
  })

  it('maps a successful ai_response to an answered event with its ids', () => {
    const { emit, events } = createHarness()

    emit(answer())

    expect(events).toEqual([
      {
        kind: 'answered',
        text: 'here you go',
        messageId: 'm-1',
        conversationId: 'c-1',
        toolCalls: [],
        toolResults: [],
        citations: [],
        truncated: false,
        configuredModel: { id: 'claude', name: 'Claude' },
      },
    ])
  })

  it('maps final response provenance and context truncation metadata', () => {
    const { emit, events } = createHarness()

    emit(
      answer({
        tool_calls: [{ id: 'call-1', name: 'get_jobs', args: { api_key: 'secret' } }],
        tool_results: [{ tool_call_id: 'call-1', name: 'get_jobs', result: { count: 2 } }],
        metadata: {
          model_id: 'model-1',
          model_name: 'Model 1',
          tool_call_rounds: 1,
          total_tool_calls: 1,
          preprocessing_applied: true,
          kb_sources: [
            { document_id: 'doc-1', title: 'Jobs', source: 'kb://jobs', url: 'https://example.com/jobs', score: 0.9 },
          ],
        },
      }),
    )

    expect(events[0]).toMatchObject({
      kind: 'answered',
      truncated: true,
      toolCalls: [{ id: 'call-1', name: 'get_jobs', arguments: { api_key: 'secret' } }],
      toolResults: [{ toolCallId: 'call-1', name: 'get_jobs', result: { count: 2 }, error: null }],
      citations: [{ documentId: 'doc-1', title: 'Jobs', source: 'kb://jobs', url: 'https://example.com/jobs', score: 0.9 }],
    })
  })

  it('maps the ai_response error variant and the error frame to failed', () => {
    const { emit, events } = createHarness()

    emit(answer({ error: true, message: 'model unavailable' }))
    emit({ type: 'error', timestamp: TIMESTAMP, message: 'bad request' })

    expect(events).toEqual([
      { kind: 'failed', text: 'model unavailable' },
      { kind: 'failed', text: 'bad request' },
    ])
  })

  it('surfaces typing frames verbatim so P1 can be observed without applying them', () => {
    const { emit, events } = createHarness()

    emit({ type: 'typing', timestamp: TIMESTAMP, message: 'Hello wor' })

    expect(events).toEqual([{ kind: 'typing', text: 'Hello wor' }])
  })

  it('maps loaded conversation messages into persisted history', () => {
    const { emit, events } = createHarness()

    emit({
      type: 'conversation_loaded',
      timestamp: TIMESTAMP,
      conversation_id: 'c-2',
      conversation: {},
      messages: [
        {
          message_id: 'm-2',
          role: 'user',
          content: 'Show the Stream triad results',
          timestamp: TIMESTAMP,
          tool_calls: [],
          tool_results: [],
          metadata: {},
        },
      ],
    })

    expect(events).toEqual([
      {
        kind: 'history-loaded',
        conversationId: 'c-2',
        messages: [
          {
            id: 'm-2',
            role: 'user',
            text: 'Show the Stream triad results',
            at: expect.objectContaining({ epochMilliseconds: Date.parse(TIMESTAMP) }),
            activity: null,
          },
        ],
      },
    ])
  })

  it('maps backend history without per-message ids or timestamps', () => {
    const { emit, events } = createHarness()

    emit({
      type: 'conversation_loaded',
      timestamp: TIMESTAMP,
      conversation_id: 'c-2',
      conversation: {},
      messages: [
        {
          message_id: null,
          role: 'assistant',
          content: 'Persisted answer',
          timestamp: null,
          tool_calls: [],
          tool_results: [],
          metadata: {},
        },
      ],
    })

    expect(events).toEqual([
      {
        kind: 'history-loaded',
        conversationId: 'c-2',
        messages: [
          {
            id: null,
            role: 'assistant',
            text: 'Persisted answer',
            at: expect.objectContaining({ epochMilliseconds: Date.parse(TIMESTAMP) }),
            activity: null,
          },
        ],
      },
    ])
  })

  // The checkpoint is the graph's raw message list. The legacy client rendered only user/assistant
  // text; this keeps that and folds the tool rounds into the answer as the live frame would.
  it('folds a raw checkpoint into user and assistant text with tool activity on the answer', () => {
    const { emit, events } = createHarness()
    const history = (
      overrides: Partial<Extract<ServerFrame, { type: 'conversation_loaded' }>['messages'][number]>,
    ) => ({
      message_id: null,
      role: 'assistant',
      content: '',
      timestamp: null,
      tool_calls: [],
      tool_results: [],
      metadata: {},
      ...overrides,
    })

    emit({
      type: 'conversation_loaded',
      timestamp: '2026-08-28T12:00:00Z',
      conversation_id: 'c-3',
      conversation: {},
      messages: [
        history({ role: 'system', content: 'You are the Workload Analyzer assistant.' }),
        history({ role: 'user', content: 'What is the status of job 42?' }),
        history({
          message_id: 'm-call',
          timestamp: '2026-08-28T10:00:01Z',
          tool_calls: [{ id: 'call-1', name: 'get_job', args: { id: 42 } }],
        }),
        history({
          role: 'tool',
          content: '[{"JobRequestID": "42"}]',
          timestamp: '2026-08-28T10:00:02Z',
          tool_results: [{ tool_call_id: 'call-1', name: 'get_job', result: { JobRequestID: '42' } }],
        }),
        history({ message_id: 'm-answer', content: 'Job 42 is complete.', timestamp: '2026-08-28T10:00:03Z' }),
        history({ role: 'user', content: 'Thanks' }),
        history({ message_id: 'm-bye', content: 'Any time.', timestamp: '2026-08-28T10:00:05Z' }),
      ],
    })

    const loaded = events[0]
    if (loaded?.kind !== 'history-loaded') {
      throw new Error('expected history-loaded')
    }

    expect(loaded.messages.map((message) => [message.role, message.text, message.at.toIso()])).toEqual([
      ['user', 'What is the status of job 42?', '2026-08-28T10:00:01.000Z'],
      ['assistant', 'Job 42 is complete.', '2026-08-28T10:00:03.000Z'],
      ['user', 'Thanks', '2026-08-28T10:00:05.000Z'],
      ['assistant', 'Any time.', '2026-08-28T10:00:05.000Z'],
    ])
    expect(loaded.messages[1]).toMatchObject({
      id: 'm-answer',
      activity: {
        toolCalls: [{ id: 'call-1', name: 'get_job', arguments: { id: 42 } }],
        toolResults: [{ toolCallId: 'call-1', name: 'get_job', result: { JobRequestID: '42' }, error: null }],
      },
    })
    expect(loaded.messages[3]?.activity).toBeNull()
  })

  it('keeps an unfinished tool round visible as an empty answer carrying its activity', () => {
    const { emit, events } = createHarness()

    emit({
      type: 'conversation_loaded',
      timestamp: TIMESTAMP,
      conversation_id: 'c-4',
      conversation: {},
      messages: [
        { message_id: null, role: 'user', content: 'Run it', timestamp: null, tool_calls: [], tool_results: [], metadata: {} },
        {
          message_id: 'm-call',
          role: 'assistant',
          content: '',
          timestamp: TIMESTAMP,
          tool_calls: [{ id: 'call-1', name: 'run_job', args: {} }],
          tool_results: [],
          metadata: {},
        },
      ],
    })

    expect(events[0]).toMatchObject({
      kind: 'history-loaded',
      messages: [
        { role: 'user', text: 'Run it', activity: null },
        { role: 'assistant', text: '', activity: { toolCalls: [{ id: 'call-1', name: 'run_job' }], toolResults: [] } },
      ],
    })
  })

  it('drops history rejected by the conversation selection coordinator', () => {
    const send = jest.fn<(frame: string) => 'sent' | 'dropped-closed'>(() => 'sent')
    let subscriber: ServerFrameSubscriber | undefined
    const gateway = new WsMessagingGateway(
      { send },
      {
        subscribe: (next) => {
          subscriber = next
          return () => { subscriber = undefined }
        },
      },
      (conversationId) => conversationId === 'current',
    )
    const events: MessagingEvent[] = []
    gateway.onEvent((event) => events.push(event))

    subscriber?.({
      type: 'conversation_loaded',
      timestamp: TIMESTAMP,
      conversation_id: 'stale',
      conversation: {},
      messages: [],
    })

    expect(events).toEqual([])
  })

  it('ignores frames owned by other contexts and ones this slice does not use', () => {
    const { emit, events } = createHarness()

    emit({ type: 'pong', timestamp: TIMESTAMP })
    emit({ type: 'auth_configured', timestamp: TIMESTAMP, message: 'ok', auth_type: 'sso' })

    expect(events).toEqual([])
  })
})
