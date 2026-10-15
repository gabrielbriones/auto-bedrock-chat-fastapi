import { act, render, screen, waitFor } from '@testing-library/react'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { describe, expect, it } from '@jest/globals'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { createContainer } from '@/app/bootstrap/container'
import { toChatBootstrap } from '@/app/bootstrap/bootstrap-config.dto'
import { createAppRouter } from '@/app/router'
import { isOk } from '@/shared/kernel/result'
import type { SocketConnection } from '@/shared/ws/socket-client'

import { bootstrapConfigFixture } from '../bootstrap/bootstrap-config.fixture'
import { silentLogger } from '../../domains/conversation/application/conversation.store.fixture'

const AT = '2026-10-14T17:54:47Z'
const CONVERSATION = 'c-1'
const ANSWER = 'IPC is 1.42 on the measured window.'

// The live region repeats the latest answer, so the transcript copy is the non-sr-only one.
const answerBubble = () =>
  screen.queryAllByText(ANSWER).find((element) => !element.classList.contains('sr-only'))

// A server that answers the handshake with SSO pre-auth and every load with the same history.
class SsoServerSocket implements SocketConnection {
  onclose: ((event: CloseEvent) => void) | null = null
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null
  onopen: ((event: Event) => void) | null = null
  #closed = false

  open(): void {
    this.onopen?.({} as Event)
  }

  // A separate task from `open` in a browser, so React renders the state in between.
  handshake(): void {
    this.#frame({ type: 'auth_configured', message: 'Authenticated as Ada', auth_type: 'sso', display_name: 'Ada' })
    this.#frame({ type: 'connection_established', session_id: 's', message: 'Connected' })
  }

  send(raw: string): void {
    const frame = JSON.parse(raw) as { type: string; conversation_id?: string }
    queueMicrotask(() => {
      if (frame.type === 'conversation_list') {
        this.#frame({
          type: 'conversation_list',
          conversations: [{ id: CONVERSATION, title: 'ISIM analysis', updated_at: AT, message_count: 1 }],
        })
      }
      if (frame.type === 'conversation_load') {
        this.#frame({
          type: 'conversation_loaded',
          conversation_id: frame.conversation_id,
          conversation: {},
          messages: [
            { message_id: 'm-1', role: 'assistant', content: ANSWER, timestamp: AT, tool_calls: [], tool_results: [], metadata: {} },
          ],
          in_progress: false,
        })
      }
    })
  }

  close(): void {
    if (this.#closed) return
    this.#closed = true
    this.onclose?.({} as CloseEvent)
  }

  #frame(data: Record<string, unknown>): void {
    if (!this.#closed) this.onmessage?.({ data: JSON.stringify({ timestamp: AT, ...data }) } as MessageEvent<unknown>)
  }
}

const renderSsoApp = () => {
  const bootstrap = toChatBootstrap({ ...bootstrapConfigFixture, ssoEnabled: true, ssoAuthenticated: true, ssoUserDisplay: 'Ada' })
  if (!isOk(bootstrap)) throw new Error('fixture must parse')

  const sockets: SsoServerSocket[] = []
  const container = createContainer(bootstrap.value, {
    logger: silentLogger,
    connectivity: { status: () => 'online', subscribe: () => () => {} },
    socketFactory: () => {
      const socket = new SsoServerSocket()
      sockets.push(socket)
      return socket
    },
  })
  const router = createAppRouter(container, {
    history: createMemoryHistory({ initialEntries: [`/chat/ui/c/${CONVERSATION}`] }),
  })
  render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
    </ContainerContext.Provider>,
  )

  return { container, sockets, router }
}

describe('a dropped socket under SSO', () => {
  // XMGPLAT-11999: the reconnect briefly signed the principal out, which hid the conversations and
  // wiped the transcript to the welcome screen until a reload happened to win the race.
  it('keeps the conversation on screen across the reconnect', async () => {
    const { container, sockets, router } = renderSsoApp()
    act(() => { container.socket.connect() })
    act(() => { sockets[0]!.open() })
    act(() => { sockets[0]!.handshake() })
    await waitFor(() => expect(answerBubble()).toBeVisible())

    const hidden: boolean[] = []
    const stop = container.conversations.subscribe(() => {
      hidden.push(!container.conversations.getSnapshot().visible)
    })
    const wiped: boolean[] = []
    const stopTranscript = container.chatSession.subscribe(() => {
      wiped.push(container.chatSession.getSnapshot().transcript.length === 0)
    })

    act(() => { sockets[0]!.close() })
    await waitFor(() => expect(sockets).toHaveLength(2), { timeout: 5000 })
    act(() => { sockets[1]!.open() })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
    act(() => { sockets[1]!.handshake() })
    await waitFor(() => expect(container.identity.getSnapshot().status).toBe('authenticated'))

    expect(hidden).not.toContain(true)
    expect(wiped).not.toContain(true)
    expect(answerBubble()).toBeVisible()
    expect(router.state.location.pathname).toBe(`/ui/c/${CONVERSATION}`)

    stop()
    stopTranscript()
    container.socket.dispose()
  }, 10_000)
})
