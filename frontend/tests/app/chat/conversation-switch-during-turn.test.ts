import { describe, expect, it } from '@jest/globals'

import { createContainer } from '@/app/bootstrap/container'
import { toChatBootstrap } from '@/app/bootstrap/bootstrap-config.dto'
import { conversationId } from '@/shared/kernel/branded'
import { isOk } from '@/shared/kernel/result'
import type { SocketConnection } from '@/shared/ws/socket-client'

import { bootstrapConfigFixture } from '../bootstrap/bootstrap-config.fixture'
import { silentLogger } from '../../domains/conversation/application/conversation.store.fixture'

const AT = '2026-10-16T12:00:00Z'

// A server that answers every load with a one-message history naming the conversation.
class ScriptedServerSocket implements SocketConnection {
  onclose: ((event: CloseEvent) => void) | null = null
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null
  onopen: ((event: Event) => void) | null = null

  open(): void {
    this.onopen?.({} as Event)
  }

  send(raw: string): void {
    const frame = JSON.parse(raw) as { type: string; conversation_id?: string }
    if (frame.type === 'conversation_load') {
      queueMicrotask(() => {
        this.frame({
          type: 'conversation_loaded',
          conversation_id: frame.conversation_id,
          conversation: {},
          messages: [
            {
              message_id: `m-${frame.conversation_id}`,
              role: 'assistant',
              content: `history of ${frame.conversation_id}`,
              timestamp: AT,
              tool_calls: [],
              tool_results: [],
              metadata: {},
            },
          ],
          in_progress: false,
        })
      })
    }
  }

  close(): void {
    this.onclose?.({} as CloseEvent)
  }

  frame(data: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify({ timestamp: AT, ...data }) } as MessageEvent<unknown>)
  }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

const aSignedInContainer = () => {
  const bootstrap = toChatBootstrap({ ...bootstrapConfigFixture, ssoEnabled: true, ssoAuthenticated: true, ssoUserDisplay: 'Ada' })
  if (!isOk(bootstrap)) throw new Error('fixture must parse')

  const sockets: ScriptedServerSocket[] = []
  const container = createContainer(bootstrap.value, {
    logger: silentLogger,
    connectivity: { status: () => 'online', subscribe: () => () => {} },
    socketFactory: () => {
      const socket = new ScriptedServerSocket()
      sockets.push(socket)
      return socket
    },
  })
  container.socket.connect()
  const socket = sockets[0]!
  socket.open()
  socket.frame({ type: 'auth_configured', message: 'Authenticated as Ada', auth_type: 'sso', display_name: 'Ada' })
  socket.frame({ type: 'connection_established', session_id: 's', message: 'Connected' })
  // `useConversationVisibility` does this in the app.
  container.conversations.setAuthenticated(true)

  return { container, socket }
}

const transcriptText = (container: ReturnType<typeof aSignedInContainer>['container']) =>
  container.chatSession.getSnapshot().transcript.map((entry) => entry.message.raw)

describe('switching conversations while a turn runs', () => {
  // XMGPLAT-11999: the server keeps reading the socket during a turn, so the user can open another
  // conversation before the first one answers. That answer used to land in the new transcript.
  it('keeps the first conversation’s progress and answer out of the one opened meanwhile', async () => {
    const { container, socket } = aSignedInContainer()
    container.conversations.open(conversationId('a'))
    await flush()
    expect(transcriptText(container)).toEqual(['history of a'])

    expect(container.chatSession.send('analyse job 42')).toBe('sent')
    socket.frame({ type: 'typing', message: 'AI is thinking...' })

    container.conversations.open(conversationId('b'))
    await flush()
    socket.frame({ type: 'typing', message: 'Calling get_jobs... (1/1)', conversation_id: 'a' })
    socket.frame({
      type: 'ai_response',
      message_id: 'm-answer',
      message: 'answer for a',
      tool_calls: [],
      tool_results: [],
      metadata: { model_id: 'm', model_name: 'M', tool_call_rounds: 1, total_tool_calls: 1, preprocessing_applied: false },
      conversation_id: 'a',
    })

    expect(transcriptText(container)).toEqual(['history of b'])
    expect(container.chatSession.getSnapshot().awaitingResponse).toBe(false)

    container.socket.dispose()
  })
})
