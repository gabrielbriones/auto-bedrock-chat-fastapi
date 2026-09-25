import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, jest } from '@jest/globals'
import { axe } from 'jest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { fakeContainer } from '../../../app/bootstrap/container.fixture'
import type { Container } from '@/app/bootstrap/container'
import { ChatSessionStore } from '@/domains/messaging/application/chat-session.store'
import type { ConnectionSource, MessagingEvent } from '@/domains/messaging/application/ports'
import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import { SystemClock } from '@/shared/kernel/instant'
import type { ConnectionState } from '@/shared/ws/socket-client'

import { ChatPanel } from '@/domains/messaging/presentation/ChatPanel'

const OPEN: ConnectionState = { status: 'open', attempt: 0, nextRetryAt: null }

const renderPanel = (
  sendResult: 'sent' | 'dropped-closed' = 'sent',
  connectionState: ConnectionState = OPEN,
  inputEnabled = true,
) => {
  const sendChat = jest.fn<(text: string) => 'sent' | 'dropped-closed'>(() => sendResult)
  const notifications = new RecordingNotificationPort()
  let emit: ((event: MessagingEvent) => void) | undefined
  let changeConnection: ((state: ConnectionState) => void) | undefined

  const connection: ConnectionSource = {
    state: connectionState,
    onStateChange: (callback) => {
      changeConnection = callback
      return () => {}
    },
  }

  const chatSession = new ChatSessionStore({
    clock: new SystemClock(),
    logger: { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() },
    connection,
    gateway: {
      sendChat,
      onEvent: (callback) => {
        emit = callback
        return () => {}
      },
    },
  })

  const container: Container = fakeContainer({ chatSession, notifications })

  const view = render(
    <ContainerContext.Provider value={container}>
      <ChatPanel inputEnabled={inputEnabled} />
    </ContainerContext.Provider>,
  )

  return {
    ...view,
    notifications,
    sendChat,
    answer(text: string) {
      emit?.({
        kind: 'answered',
        text,
        messageId: 'm-1',
        conversationId: 'c-1',
        toolCalls: [],
        toolResults: [],
        citations: [],
        truncated: false,
        configuredModel: { id: 'claude', name: 'Claude' },
      })
    },
    typing(text: string) {
      emit?.({ kind: 'typing', text })
    },
    reconnect(state: ConnectionState) {
      changeConnection?.(state)
    },
  }
}

describe('ChatPanel', () => {
  it('echoes a sent message and renders the ai_response as an assistant message', async () => {
    const user = userEvent.setup()
    const { answer, sendChat } = renderPanel()

    await user.type(screen.getByRole('textbox', { name: 'Message' }), 'analyse job 42')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(sendChat).toHaveBeenCalledWith('analyse job 42')
    expect(screen.getByRole('article', { name: 'You' })).toHaveTextContent('analyse job 42')
    expect(screen.queryByText('AI is typing...')).not.toBeInTheDocument()

    answer('IPC is 2.1')

    expect(await screen.findByRole('article', { name: 'Assistant' })).toHaveTextContent('IPC is 2.1')
    expect(screen.getAllByRole('article', { name: 'You' })).toHaveLength(1)
  })

  // FIX-17: the streaming bubble renders the cumulative snapshot itself, not a static string.
  it('renders the cumulative typing snapshot after a WebSocket typing event', async () => {
    const user = userEvent.setup()
    const { typing } = renderPanel()

    await user.type(screen.getByRole('textbox', { name: 'Message' }), 'hello{Enter}')
    typing('Hel')
    typing('Hello there')

    await waitFor(() => {
      expect(screen.getByText('Hello there')).toBeInTheDocument()
    })
    expect(screen.queryByText('Hel')).not.toBeInTheDocument()
  })

  // FR-MSG-012 / FR-MSG-024: each lock reason closes the composer and states why.
  it.each<[string, () => Promise<void> | void, (field: HTMLElement) => void]>([
    [
      'a turn is awaiting a response',
      async () => {
        renderPanel()
        await userEvent.setup().type(screen.getByRole('textbox', { name: 'Message' }), 'hello{Enter}')
      },
      (field) => expect(field).toHaveAttribute('placeholder', 'Waiting for response...'),
    ],
    [
      'the socket is not open',
      () => {
        renderPanel('sent', { status: 'reconnecting', attempt: 2, nextRetryAt: null })
      },
      (field) => expect(field).toHaveAccessibleDescription(MESSAGING_COPY.composer.disabled.offline),
    ],
    [
      'input is not permitted by the composition root',
      () => {
        renderPanel('sent', OPEN, false)
      },
      (field) =>
        expect(field).toHaveAccessibleDescription(MESSAGING_COPY.composer.disabled.unauthenticated),
    ],
  ])('disables the composer while %s', async (_reason, arrange, statesWhy) => {
    await arrange()

    const field = screen.getByRole('textbox', { name: 'Message' })
    expect(field).toBeDisabled()
    statesWhy(field)
  })

  // FR-MSG-005/012: a turn recycled by a drop no longer awaits anything, so the composer must
  // reopen — and hand the keyboard back — the moment the socket is available again.
  it('reopens the composer with focus once a recycled turn unlocks it', async () => {
    const user = userEvent.setup()
    const { reconnect } = renderPanel()

    await user.type(screen.getByRole('textbox', { name: 'Message' }), 'hello{Enter}')
    expect(screen.getByRole('textbox', { name: 'Message' })).toBeDisabled()

    reconnect({ status: 'reconnecting', attempt: 1, nextRetryAt: null })
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Message' })).toHaveAccessibleDescription(
        MESSAGING_COPY.composer.disabled.offline,
      )
    })

    reconnect(OPEN)

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Message' })).toBeEnabled()
    })
    expect(screen.getByRole('textbox', { name: 'Message' })).toHaveFocus()
  })

  it('notifies rather than silently swallowing a dropped send (ADR-004)', async () => {
    const user = userEvent.setup()
    const { notifications } = renderPanel('dropped-closed')

    await user.type(screen.getByRole('textbox', { name: 'Message' }), 'hello{Enter}')

    expect(notifications.notifications).toContainEqual(
      expect.objectContaining({ kind: 'error' }),
    )
  })

  // NFR-A11Y-001
  it('has no axe violations', async () => {
    const user = userEvent.setup()
    const { answer, container } = renderPanel()

    await user.type(screen.getByRole('textbox', { name: 'Message' }), 'hello{Enter}')
    answer('hi there')

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
