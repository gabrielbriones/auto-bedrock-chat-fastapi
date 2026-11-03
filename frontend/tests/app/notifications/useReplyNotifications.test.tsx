import { describe, expect, it, jest } from '@jest/globals'
import { act, render, screen, waitFor } from '@testing-library/react'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'

import { fakeContainer } from '../bootstrap/container.fixture'
import { ContainerContext } from '@/app/bootstrap/container-context'
import { createAppRouter } from '@/app/router'
import { ReplyNotificationStore } from '@/app/notifications/reply-notification.store'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import { conversationId } from '@/shared/kernel/branded'
import { SystemClock } from '@/shared/kernel/instant'
import type { ConnectionState } from '@/shared/ws/socket-client'
import { ChatSessionStore } from '@/domains/messaging/application/chat-session.store'
import type { MessagingEvent } from '@/domains/messaging/application/ports'
import {
  FakePageAttentionPort,
  FakeSystemNotificationPort,
  InMemoryPreferenceStorage,
} from '../../shared/ports/fake-reply-notification-ports'

const OPEN: ConnectionState = { status: 'open', attempt: 0, nextRetryAt: null }

const renderAt = (path: string) => {
  let emit: ((event: MessagingEvent) => void) | undefined
  const chatSession = new ChatSessionStore({
    clock: new SystemClock(),
    logger: { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() },
    gateway: {
      sendChat: () => 'sent',
      onEvent: (callback) => {
        emit = callback
        return () => {}
      },
    },
    connection: { state: OPEN, onStateChange: () => () => {} },
  })
  const system = new FakeSystemNotificationPort('granted')
  const attention = new FakePageAttentionPort(false)
  const replyNotifications = new ReplyNotificationStore({
    chatSession,
    system,
    attention,
    storage: new InMemoryPreferenceStorage(),
    describe: (reply) => ({ conversationId: reply.conversationId, title: 'Job 42' }),
  })
  const container = fakeContainer({ replyNotifications })
  const router = createAppRouter(container, { history: createMemoryHistory({ initialEntries: [path] }) })

  render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
    </ContainerContext.Provider>,
  )

  const reply = (id: string) => {
    act(() => {
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
    })
  }

  return { router, system, attention, reply, appTitle: container.bootstrap.appTitle }
}

describe('useReplyNotifications (FR-MSG-032)', () => {
  it('shows an unread count in the tab title while away and clears it on return', async () => {
    const { attention, reply, appTitle } = renderAt('/chat/ui/c/abc-123')
    await screen.findByRole('textbox', { name: MESSAGING_COPY.composer.label })

    reply('abc-123')
    await waitFor(() => { expect(document.title).toBe(`(1) ${appTitle}`) })

    act(() => { attention.set(true) })
    await waitFor(() => { expect(document.title).toBe(appTitle) })
  })

  it('opens the conversation that received the reply when the notification is clicked', async () => {
    const { router, system, reply } = renderAt('/chat/ui/c/abc-123')
    await screen.findByRole('textbox', { name: MESSAGING_COPY.composer.label })

    reply('c-9')
    act(() => { system.shown[0]?.onClick() })

    await waitFor(() => { expect(router.state.location.pathname).toBe('/ui/c/c-9') })
  })

  it('stays put when the reply belongs to the conversation already on screen', async () => {
    const { router, system, reply } = renderAt('/chat/ui/c/abc-123')
    await screen.findByRole('textbox', { name: MESSAGING_COPY.composer.label })

    reply(conversationId('abc-123'))
    act(() => { system.shown[0]?.onClick() })

    expect(router.state.location.pathname).toBe('/ui/c/abc-123')
  })
})
