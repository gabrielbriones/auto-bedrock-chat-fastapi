import { describe, expect, it, jest } from '@jest/globals'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'

import { fakeContainer } from '../bootstrap/container.fixture'
import { bootstrapConfigFixture } from '../bootstrap/bootstrap-config.fixture'
import { toChatBootstrap } from '@/app/bootstrap/bootstrap-config.dto'
import { createContainer } from '@/app/bootstrap/container'
import { ContainerContext } from '@/app/bootstrap/container-context'
import { createAppRouter } from '@/app/router'
import { ReplyNotificationStore } from '@/app/notifications/reply-notification.store'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import { conversationId } from '@/shared/kernel/branded'
import { SystemClock } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'
import type { SystemNotificationPermission } from '@/shared/ports/system-notification-port'
import type { ConnectionState, SocketConnection } from '@/shared/ws/socket-client'
import { ChatSessionStore } from '@/domains/messaging/application/chat-session.store'
import type { MessagingEvent } from '@/domains/messaging/application/ports'
import {
  FakePageAttentionPort,
  FakeSystemNotificationPort,
  InMemoryPreferenceStorage,
} from '../../shared/ports/fake-reply-notification-ports'

const OPEN: ConnectionState = { status: 'open', attempt: 0, nextRetryAt: null }

const renderAt = (path: string, permission: SystemNotificationPermission = 'granted') => {
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
  const system = new FakeSystemNotificationPort(permission)
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

  const reply = (id: string | null) => {
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

// A server that completes the handshake, so the composer is live and a real user send can happen.
class HandshakeSocket implements SocketConnection {
  onclose: ((event: CloseEvent) => void) | null = null
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null
  onopen: ((event: Event) => void) | null = null

  handshake(): void {
    this.onopen?.({} as Event)
    this.onmessage?.({ data: JSON.stringify({ type: 'connection_established', session_id: 's', message: 'Connected' }) } as MessageEvent<unknown>)
  }

  send(): void {}

  close(): void {
    this.onclose?.({} as CloseEvent)
  }
}

const connectedApp = (system: FakeSystemNotificationPort) => {
  const bootstrap = toChatBootstrap(bootstrapConfigFixture)
  if (!isOk(bootstrap)) throw new Error('fixture must parse')

  const socket = new HandshakeSocket()
  const container = createContainer(bootstrap.value, {
    logger: { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() },
    connectivity: { status: () => 'online', subscribe: () => () => {} },
    socketFactory: () => socket,
    systemNotifications: system,
    pageAttention: new FakePageAttentionPort(true),
    preferenceStorage: new InMemoryPreferenceStorage(),
  })
  const router = createAppRouter(container, { history: createMemoryHistory({ initialEntries: ['/chat/ui'] }) })

  render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
    </ContainerContext.Provider>,
  )

  return { container, socket }
}

describe('reply notifications in the app shell (FR-MSG-032)', () => {
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
    const navigate = jest.spyOn(router, 'navigate')

    reply(conversationId('abc-123'))
    act(() => { system.shown[0]?.onClick() })

    expect(navigate).not.toHaveBeenCalled()
    expect(router.state.location.pathname).toBe('/ui/c/abc-123')
  })

  it('opens the conversation from the admin dashboard, where the chat layout is not mounted', async () => {
    const { router, system, reply } = renderAt('/chat/dashboard')
    await waitFor(() => { expect(router.state.location.pathname).toBe('/dashboard') })
    await waitFor(() => { expect(router.state.status).toBe('idle') })

    reply('c-9')
    act(() => { system.shown[0]?.onClick() })

    await waitFor(() => { expect(router.state.location.pathname).toBe('/ui/c/c-9') })
  })

  it('returns to the unsaved chat from the dashboard when the reply has no conversation yet', async () => {
    const { router, system, reply } = renderAt('/chat/dashboard')
    await waitFor(() => { expect(router.state.location.pathname).toBe('/dashboard') })
    await waitFor(() => { expect(router.state.status).toBe('idle') })

    reply(null)
    act(() => { system.shown[0]?.onClick() })

    await waitFor(() => { expect(router.state.location.pathname).toBe('/ui') })
  })

  it('asks for notification permission on the first message the user sends', async () => {
    const system = new FakeSystemNotificationPort('default')
    const { container, socket } = connectedApp(system)
    act(() => { container.socket.connect() })
    act(() => { socket.handshake() })

    const composer = await screen.findByRole('textbox', { name: MESSAGING_COPY.composer.label })
    await waitFor(() => { expect(composer).toBeEnabled() })
    expect(system.requests).toBe(0)

    await userEvent.type(composer, 'analyse job 42{Enter}')

    await waitFor(() => { expect(system.requests).toBe(1) })
    container.socket.dispose()
  })
})
