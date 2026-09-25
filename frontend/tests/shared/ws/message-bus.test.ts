import { describe, expect, it, jest } from '@jest/globals'

import type { Logger } from '@/shared/logging/logger'
import { FRAME_OWNER, MessageBus, ServerFrameSchema } from '@/shared/ws/message-bus'

const logger = (): Logger => ({ debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() })

describe('ServerFrameSchema', () => {
  it('parses contract frames with their required envelope and payload fields', () => {
    expect(
      ServerFrameSchema.safeParse({
        type: 'connection_established',
        timestamp: '2026-08-25T12:00:00Z',
        session_id: 'session-1',
        message: 'Connected',
      }).success,
    ).toBe(true)
  })

  it('requires ai response data unless the frame is an error variant', () => {
    expect(
      ServerFrameSchema.safeParse({
        type: 'ai_response',
        timestamp: '2026-08-25T12:00:00Z',
        message: 'response',
      }).success,
    ).toBe(false)
    expect(
      ServerFrameSchema.safeParse({
        type: 'ai_response',
        timestamp: '2026-08-25T12:00:00Z',
        message: 'response failed',
        error: true,
      }).success,
    ).toBe(true)
  })

  // The variants below are all shapes the backend really emits. Live-backend regression
  // (2026-08-31): it copies its own per-message metadata (message_id/usage/timestamp) into the
  // wire metadata verbatim, and any of the nullable fields (`stop_reason`, usage tokens, the KB
  // `source_url` column) can arrive as null — `.strict()` rejected every real frame until then,
  // freezing the UI on "AI is typing" forever.
  it.each([
    [
      'without optional knowledge-base metadata',
      {
        message_id: 'message-1',
        usage: { input_tokens: 10, output_tokens: 5 },
        timestamp: '2026-08-25T12:00:00Z',
        model_id: 'model-1',
        model_name: 'Test model',
        tool_call_rounds: 0,
        total_tool_calls: 0,
        preprocessing_applied: false,
      },
    ],
    [
      'in the real shape observed from a live backend',
      {
        message_id: 'ad001382-5c05-40ae-92dd-7d3d321f2154',
        model_id: 'us.anthropic.claude-sonnet-5',
        usage: { input_tokens: 6143, output_tokens: 102 },
        stop_reason: 'end_turn',
        timestamp: '2026-08-31T14:07:53.902641',
        model_name: 'Claude Sonnet 5 (US)',
        tool_call_rounds: 0,
        total_tool_calls: 0,
        preprocessing_applied: false,
        input_tokens: 6143,
        output_tokens: 102,
      },
    ],
    [
      'with the nullable fields the backend can emit',
      {
        message_id: 'message-1',
        model_id: 'model-1',
        model_name: 'Test model',
        usage: { input_tokens: null, output_tokens: null },
        stop_reason: null,
        timestamp: '2026-08-31T14:07:53.902641',
        tool_call_rounds: 0,
        total_tool_calls: 0,
        preprocessing_applied: false,
        kb_used: true,
        kb_chunks: 1,
        kb_sources: [
          { document_id: 'doc-1', title: 'Doc', source: 'kb', url: null, score: 0.42 },
        ],
      },
    ],
  ])('accepts an ai response with metadata %s', (_label, metadata) => {
    expect(
      ServerFrameSchema.safeParse({
        type: 'ai_response',
        message_id: 'message-1',
        message: 'response',
        tool_calls: [],
        tool_results: [],
        timestamp: '2026-08-31T14:07:53.914062',
        metadata,
        conversation_id: 'conversation-1',
      }).success,
    ).toBe(true)
  })

  it('accepts an ai response whose metadata omits the graph-copied fields', () => {
    expect(
      ServerFrameSchema.safeParse({
        type: 'ai_response',
        message_id: 'message-1',
        message: 'answer',
        tool_calls: [],
        tool_results: [],
        timestamp: '2026-08-31T14:07:53.914062',
        metadata: {
          model_id: 'model-1',
          model_name: 'Test model',
          tool_call_rounds: 0,
          total_tool_calls: 0,
          preprocessing_applied: false,
        },
        conversation_id: 'conversation-1',
      }).success,
    ).toBe(true)
  })

  it('accepts nullable identifiers and timestamps in loaded conversation history', () => {
    expect(
      ServerFrameSchema.safeParse({
        type: 'conversation_loaded',
        timestamp: '2026-09-17T12:00:00Z',
        conversation_id: 'conversation-1',
        conversation: {},
        messages: [
          {
            message_id: null,
            role: 'user',
            content: 'Analyse this workload',
            timestamp: null,
            tool_calls: [],
            tool_results: [],
            metadata: {},
          },
        ],
      }).success,
    ).toBe(true)
  })

  it('maps every contract frame type to its owning context', () => {
    expect(FRAME_OWNER).toEqual({
      connection_established: 'messaging',
      auth_configured: 'iam',
      auth_failed: 'iam',
      auth_expired: 'iam',
      logout_success: 'iam',
      typing: 'messaging',
      ai_response: 'messaging',
      error: 'messaging',
      pong: 'messaging',
      history: 'messaging',
      history_cleared: 'messaging',
      feedback_ack: 'feedback',
      feedback_error: 'feedback',
      config_updated: 'model-config',
      conversation_created: 'conversation',
      conversation_titled: 'conversation',
      conversation_list: 'conversation',
      conversation_loaded: 'conversation',
      conversation_renamed: 'conversation',
      conversation_deleted: 'conversation',
      conversation_bulk_deleted: 'conversation',
      conversation_all_deleted: 'conversation',
      conversation_error: 'conversation',
    })
  })
})

describe('MessageBus', () => {
  // The per-reason logging is asserted in the dedicated tests below; this one only proves that
  // a bad frame never poisons delivery of the valid frames that follow it.
  it('keeps delivering valid frames after malformed, unknown, and schema-invalid ones', () => {
    const bus = new MessageBus(logger())
    const subscriber = jest.fn()
    bus.subscribe(subscriber)

    bus.receive('{invalid json')
    bus.receive(new Blob(['not a text frame']))
    bus.receive(JSON.stringify({ type: 'not_in_the_contract', timestamp: '2026-08-25T12:00:00Z' }))
    bus.receive(JSON.stringify({ type: 'connection_established', timestamp: '2026-08-25T12:00:00Z' }))
    bus.receive(
      JSON.stringify({
        type: 'connection_established',
        timestamp: '2026-08-25T12:00:00Z',
        session_id: 'session-1',
        message: 'Connected',
      }),
    )

    expect(subscriber).toHaveBeenCalledExactlyOnceWith({
      type: 'connection_established',
      timestamp: '2026-08-25T12:00:00Z',
      session_id: 'session-1',
      message: 'Connected',
    })
  })

  it('stops delivering frames after a subscriber unsubscribes', () => {
    const bus = new MessageBus(logger())
    const subscriber = jest.fn()
    const unsubscribe = bus.subscribe(subscriber)
    unsubscribe()

    bus.receive(JSON.stringify({ type: 'pong', timestamp: '2026-08-25T12:00:00Z' }))

    expect(subscriber).not.toHaveBeenCalled()
  })

  it('counts every unknown frame and logs each type only once', () => {
    const testLogger = logger()
    const bus = new MessageBus(testLogger)
    const unknownFrame = JSON.stringify({
      type: 'not_in_the_contract',
      timestamp: '2026-08-25T12:00:00Z',
    })

    bus.receive(unknownFrame)
    bus.receive(unknownFrame)

    expect(bus.unknownFrameCount('not_in_the_contract')).toBe(2)
    expect(bus.unknownFrameCount('never_seen')).toBe(0)
    expect(testLogger.warn).toHaveBeenCalledExactlyOnceWith('ws_frame_unknown', {
      type: 'not_in_the_contract',
    })
  })

  it('treats a frame named after an Object prototype member as unknown', () => {
    const testLogger = logger()
    const bus = new MessageBus(testLogger)

    bus.receive(JSON.stringify({ type: 'toString', timestamp: '2026-08-25T12:00:00Z' }))

    expect(bus.unknownFrameCount('toString')).toBe(1)
    expect(testLogger.warn).toHaveBeenCalledExactlyOnceWith('ws_frame_unknown', { type: 'toString' })
  })

  it('reports a schema-invalid frame once per type and logs paths without payload values', () => {
    const testLogger = logger()
    const bus = new MessageBus(testLogger)
    const invalidFrame = JSON.stringify({
      type: 'connection_established',
      timestamp: '2026-08-25T12:00:00Z',
      session_id: 'session-1',
    })

    bus.receive(invalidFrame)
    bus.receive(invalidFrame)

    expect(bus.invalidFrameCount('connection_established')).toBe(2)
    expect(testLogger.warn).toHaveBeenCalledExactlyOnceWith('ws_frame_invalid', {
      type: 'connection_established',
      issues: [{ path: 'message', code: 'invalid_type' }],
    })
  })

  it('reports each malformed reason once and truncates an oversized sample', () => {
    const testLogger = logger()
    const bus = new MessageBus(testLogger)

    bus.receive(`{"type":"typing","message":"${'a'.repeat(500)}`)
    bus.receive('{invalid json')
    bus.receive(undefined)

    expect(bus.malformedFrameCount('invalid-json')).toBe(2)
    expect(bus.malformedFrameCount('non-string')).toBe(1)
    expect(testLogger.warn).toHaveBeenCalledTimes(2)
    expect(testLogger.warn).toHaveBeenNthCalledWith(1, 'ws_frame_malformed', {
      reason: 'invalid-json',
      sample: expect.stringMatching(/^.{200}\u2026$/u),
    })
    expect(testLogger.warn).toHaveBeenNthCalledWith(2, 'ws_frame_malformed', {
      reason: 'non-string',
      frame: 'undefined',
    })
  })
})