import { describe, expect, it, jest } from '@jest/globals'

import { createReplyNotifications } from '@/app/bootstrap/reply-notifications'
import { conversationId, type ConversationId } from '@/shared/kernel/branded'
import { FixedClock, Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'
import type { ConnectionState } from '@/shared/ws/socket-client'
import type { ConversationSnapshot } from '@/domains/conversation/application/conversation-state'
import { createConversation } from '@/domains/conversation/domain/conversation'
import { ChatSessionStore } from '@/domains/messaging/application/chat-session.store'
import type { MessagingEvent } from '@/domains/messaging/application/ports'
import {
  FakePageAttentionPort,
  FakeSystemNotificationPort,
  InMemoryPreferenceStorage,
} from '../../shared/ports/fake-reply-notification-ports'

const OPEN: ConnectionState = { status: 'open', attempt: 0, nextRetryAt: null }

const now = () => {
  const instant = Instant.fromIso('2026-10-30T10:00:00Z')
  if (!isOk(instant)) throw new Error('unparseable fixture instant')
  return instant.value
}

const roster = (activeId: ConversationId | null) =>
  ({
    activeId,
    items: [
      createConversation(conversationId('c-1'), now(), 'Job 42 analysis'),
      createConversation(conversationId('c-2'), now(), null),
    ],
  }) as unknown as ConversationSnapshot

const harness = (activeId: ConversationId | null) => {
  let emit: ((event: MessagingEvent) => void) | undefined
  const chatSession = new ChatSessionStore({
    clock: new FixedClock(now()),
    logger: { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() },
    gateway: { sendChat: () => 'sent', onEvent: (callback) => { emit = callback; return () => {} } },
    connection: { state: OPEN, onStateChange: () => () => {} },
  })
  const system = new FakeSystemNotificationPort('granted')

  createReplyNotifications(
    { appTitle: 'Workload Analyzer' },
    { chatSession, conversations: { getSnapshot: () => roster(activeId) } },
    { systemNotifications: system, pageAttention: new FakePageAttentionPort(false), preferenceStorage: new InMemoryPreferenceStorage() },
  )

  const answer = (id: string | null) => {
    chatSession.send('analyse')
    emit?.({
      kind: 'answered',
      text: 'Done.',
      messageId: null,
      conversationId: id,
      toolCalls: [],
      toolResults: [],
      citations: [],
      truncated: false,
      configuredModel: { id: 'm', name: 'M' },
    })
  }

  return { system, answer }
}

describe('createReplyNotifications (FR-MSG-032 / FR-CONV-020)', () => {
  it('titles the notification with the conversation the reply belongs to', () => {
    const { system, answer } = harness(null)

    answer('c-1')

    expect(system.shown[0]).toMatchObject({ title: 'Job 42 analysis', tag: 'reply:c-1' })
  })

  it('falls back to the active conversation when the reply carries no id', () => {
    const { system, answer } = harness(conversationId('c-1'))

    answer(null)

    expect(system.shown[0]).toMatchObject({ title: 'Job 42 analysis', tag: 'reply:c-1' })
  })

  it('uses the app title for an untitled or unknown conversation', () => {
    const { system, answer } = harness(null)

    answer('c-2')
    answer('c-unknown')
    answer(null)

    expect(system.shown.map((shown) => [shown.title, shown.tag])).toEqual([
      ['Workload Analyzer', 'reply:c-2'],
      ['Workload Analyzer', 'reply:c-unknown'],
      ['Workload Analyzer', 'reply:new'],
    ])
  })
})
