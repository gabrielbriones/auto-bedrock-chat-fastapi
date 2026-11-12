import { describe, expect, it, jest } from '@jest/globals'

import type { Logger } from '@/shared/logging/logger'
import { HttpClient } from '@/shared/http/http-client'

import { bootstrapConfigFixture } from './bootstrap-config.fixture'
import { FakeChatSocket } from '../../shared/ws/fake-chat-socket'
import { toChatBootstrap } from '@/app/bootstrap/bootstrap-config.dto'
import { createContainer } from '@/app/bootstrap/container'
import { isOk } from '@/shared/kernel/result'

const fakeLogger: Logger = { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() }

const chatBootstrap = () => {
  const result = toChatBootstrap(bootstrapConfigFixture)
  if (!isOk(result)) {
    throw new Error('fixture must parse for this test to be meaningful')
  }
  return result.value
}

describe('createContainer', () => {
  it('builds a container from fakes with no fetch and no DOM', () => {
    const httpClient = new HttpClient()
    const container = createContainer(chatBootstrap(), { httpClient, logger: fakeLogger })

    expect(container.httpClient).toBe(httpClient)
    expect(container.logger).toBe(fakeLogger)
    expect(container.bootstrap.adminPrefix).toBe('/chat/admin')
  })

  it('defaults to a real HttpClient and ConsoleLogger when no deps are supplied', () => {
    const container = createContainer(chatBootstrap())

    expect(container.httpClient).toBeInstanceOf(HttpClient)
    expect(container.logger).toBeDefined()
  })

  // FR-CONV-002: the contexts never import each other, so this seam is the only place "New chat"
  // can reach the transcript.
  it('clears the transcript when a new conversation is started', () => {
    const socket = new FakeChatSocket([
      { type: 'open' },
      {
        type: 'frame',
        data: JSON.stringify({
          type: 'auth_configured',
          timestamp: '2026-08-28T08:59:00Z',
          message: 'Authenticated',
          auth_type: 'api_key',
          display_name: 'Test user',
        }),
      },
      {
        type: 'frame',
        data: JSON.stringify({
          type: 'conversation_loaded',
          timestamp: '2026-08-28T10:00:00Z',
          conversation_id: 'a',
          conversation: {},
          messages: [
            {
              message_id: 'm-1',
              role: 'assistant',
              content: 'From the old thread.',
              timestamp: '2026-08-27T09:00:01Z',
              tool_calls: [],
              tool_results: [],
              metadata: {},
            },
          ],
        }),
      },
    ])
    const container = createContainer(chatBootstrap(), {
      logger: fakeLogger,
      connectivity: { status: () => 'online', subscribe: () => () => {} },
      socketFactory: () => socket,
    })
    container.socket.connect()
    socket.play()
    expect(container.chatSession.getSnapshot().transcript).toHaveLength(1)

    container.conversations.startNew()

    expect(container.chatSession.getSnapshot().transcript).toEqual([])
    container.socket.dispose()
  })

  // XMGPLAT-12228: the server detaches this tab and sends nothing more for the conversation, so a
  // turn it was following there must not leave the composer waiting for an answer.
  it.each([
    ['a single delete', { type: 'conversation_deleted', conversation_id: 'a' }],
    [
      'a bulk delete',
      {
        type: 'conversation_bulk_deleted',
        deleted_ids: ['a'],
        active_conversation_deleted: true,
        from_other_connection: true,
      },
    ],
    ['a delete-all', { type: 'conversation_all_deleted', deleted_count: 1 }],
  ])('stops following a turn when another tab removes its conversation with %s', (_, removal) => {
    const frame = (body: Record<string, unknown>) => ({
      type: 'frame' as const,
      data: JSON.stringify({ timestamp: '2026-08-28T10:00:00Z', ...body }),
    })
    const socket = new FakeChatSocket([
      { type: 'open' },
      frame({ type: 'auth_configured', message: 'Authenticated', auth_type: 'api_key', display_name: 'Test user' }),
      frame({
        type: 'conversation_loaded',
        conversation_id: 'a',
        conversation: {},
        messages: [],
        in_progress: true,
        live: true,
      }),
      frame({ type: 'turn_started', conversation_id: 'a', message: 'Validate job 42' }),
      frame(removal),
      // Whatever the turn still sends is for a conversation this tab no longer shows.
      frame({ type: 'typing', message: 'Calling get_job... (1/1)', conversation_id: 'a' }),
    ])
    const container = createContainer(chatBootstrap(), {
      logger: fakeLogger,
      connectivity: { status: () => 'online', subscribe: () => () => {} },
      socketFactory: () => socket,
    })
    container.socket.connect()
    for (let step = 0; step < 4; step += 1) {
      socket.playNext()
    }
    expect(container.chatSession.getSnapshot().awaitingResponse).toBe(true)
    expect(container.conversations.getSnapshot().activeId).toBe('a')

    socket.play()

    expect(container.conversations.getSnapshot().activeId).toBeNull()
    expect(container.chatSession.getSnapshot().awaitingResponse).toBe(false)
    expect(container.chatSession.getSnapshot().transcript).toEqual([])
    container.socket.dispose()
  })
})
