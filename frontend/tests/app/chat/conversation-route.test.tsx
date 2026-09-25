import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { describe, expect, it } from '@jest/globals'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { fakeContainer } from '../bootstrap/container.fixture'
import { createAppRouter } from '@/app/router'
import { MAIN_SCROLL_CONTAINER_ID } from '@/components/ui/composed/scroll-container'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

import { createHarness } from '../../domains/conversation/application/conversation.store.fixture'
import { aConversation, anEvent, id } from '../../domains/conversation/domain/conversation.fixture'

const ROSTER = [aConversation('a', 9, 'GEMM tuning'), aConversation('b', 0, 'Stream triad')]

const renderAt = (path: string) => {
  const harness = createHarness()
  const container = fakeContainer({ conversations: harness.store })
  const router = createAppRouter(container, {
    history: createMemoryHistory({ initialEntries: [path] }),
  })

  render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
    </ContainerContext.Provider>,
  )

  const emit = (event: Parameters<typeof harness.gateway.emit>[0]) => {
    act(() => {
      harness.gateway.emit(event)
    })
  }

  return { ...harness, container, router, emit, pathname: () => router.state.location.pathname }
}

// The router resolves the route asynchronously, so nothing may be emitted until it has mounted.
const routeReady = () => screen.findByRole('textbox', { name: MESSAGING_COPY.composer.label })

const authenticate = async (container: ReturnType<typeof fakeContainer>) => {
  act(() => {
    container.messageBus.receive(
      JSON.stringify({
        type: 'auth_configured',
        timestamp: '2026-08-28T08:59:00Z',
        message: 'Authenticated',
        auth_type: 'api_key',
        display_name: 'Test user',
      }),
    )
  })
  await waitFor(() => expect(container.conversations.getSnapshot().visible).toBe(true))
}

describe('conversation URL binding', () => {
  // FR-CONV-011: a direct visit loads that conversation.
  it('loads the conversation named by the path', async () => {
    const { gateway } = renderAt('/bedrock-chat/ui/c/conv-1')

    await waitFor(() => {
      expect(gateway.calls).toContainEqual(['load', id('conv-1')])
    })
  })

  // The regression this hook is most prone to: the URL effect reloading a stale id straight back
  // over the conversation the user just picked.
  it('does not reload the route id after the user selects another conversation', async () => {
    const { container, gateway, emit, store, pathname } = renderAt('/bedrock-chat/ui/c/conv-1')
    await routeReady()
    await authenticate(container)
    await waitFor(() => expect(gateway.methods()).toContain('load'))
    emit(anEvent.loaded('conv-1'))
    emit(anEvent.listed(ROSTER))
    gateway.calls.length = 0

    act(() => {
      store.open(id('b'))
    })
    emit(anEvent.loaded('b'))

    await waitFor(() => expect(pathname()).toBe('/ui/c/b'))
    expect(gateway.calls.filter(([method]) => method === 'load')).toEqual([['load', id('b')]])
  })

  it('does not toggle back after consecutive sidebar selections from the base route', async () => {
    const { container, gateway, emit, store, pathname } = renderAt('/bedrock-chat/ui')
    await routeReady()
    await authenticate(container)
    emit(anEvent.listed(ROSTER))

    act(() => {
      store.open(id('a'))
    })
    emit(anEvent.loaded('a'))
    await waitFor(() => expect(pathname()).toBe('/ui/c/a'))
    gateway.calls.length = 0

    act(() => {
      store.open(id('b'))
    })
    emit(anEvent.loaded('b'))

    await waitFor(() => expect(pathname()).toBe('/ui/c/b'))
    expect(gateway.calls.filter(([method]) => method === 'load')).toEqual([['load', id('b')]])
  })

  it('does not toggle when the previous conversation response arrives late', async () => {
    const { container, gateway, emit, store, pathname } = renderAt('/bedrock-chat/ui/c/a')
    await routeReady()
    await authenticate(container)
    await waitFor(() => expect(gateway.calls).toContainEqual(['load', id('a')]))

    act(() => {
      store.open(id('b'))
    })
    emit(anEvent.loaded('b'))
    await waitFor(() => expect(pathname()).toBe('/ui/c/b'))
    gateway.calls.length = 0

    emit(anEvent.loaded('a'))

    await waitFor(() => expect(pathname()).toBe('/ui/c/b'))
    expect(gateway.calls.filter(([method]) => method === 'load')).toEqual([])
  })

  it('loads a sidebar selection into the route and replaces the visible transcript', async () => {
    const { container, gateway, emit, pathname } = renderAt('/bedrock-chat/ui/c/a')
    await routeReady()
    await authenticate(container)
    emit(anEvent.loaded('a'))
    emit(anEvent.listed(ROSTER))

    act(() => {
      container.messageBus.receive(
        JSON.stringify({
          type: 'ai_response',
          timestamp: '2026-08-28T09:00:00Z',
          message: 'Answer from the current conversation',
          message_id: 'm-current',
          tool_calls: [],
          tool_results: [],
          conversation_id: 'current',
          metadata: {
            model_id: 'claude',
            model_name: 'Claude',
            tool_call_rounds: 0,
            total_tool_calls: 0,
            preprocessing_applied: false,
          },
        }),
      )
    })
    expect(await screen.findByText('Answer from the current conversation')).toBeVisible()

    const scroller = document.getElementById(MAIN_SCROLL_CONTAINER_ID)
    expect(scroller).not.toBeNull()
    let scrollTop = 100
    Object.defineProperties(scroller!, {
      scrollHeight: { configurable: true, get: () => 1000 },
      clientHeight: { configurable: true, get: () => 200 },
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value: number) => { scrollTop = value },
      },
    })
    fireEvent.scroll(scroller!)

    await userEvent.click(screen.getByRole('button', { name: 'Stream triad' }))
    expect(gateway.calls).toContainEqual(['load', id('b')])

    act(() => {
      container.messageBus.receive(
        JSON.stringify({
          type: 'conversation_loaded',
          timestamp: '2026-08-28T10:00:00Z',
          conversation_id: 'b',
          conversation: {},
          messages: [
            {
              message_id: 'm-2',
              role: 'assistant',
              content: 'The measured bandwidth was 118 GB/s.',
              timestamp: '2026-08-27T09:00:01Z',
              tool_calls: [],
              tool_results: [],
              metadata: {},
            },
          ],
        }),
      )
      emit(anEvent.loaded('b'))
    })

    await waitFor(() => expect(pathname()).toBe('/ui/c/b'))
    expect(
      screen
        .getAllByText('The measured bandwidth was 118 GB/s.')
        .find((element) => element.tagName === 'P'),
    ).toBeVisible()
    expect(screen.queryByText('Answer from the current conversation')).not.toBeInTheDocument()
    expect(scrollTop).toBe(1000)
  })

  // FR-CONV-002: a new, unsaved conversation lives at /ui.
  it('returns to the base path when a new conversation is started', async () => {
    const { emit, store, pathname } = renderAt('/bedrock-chat/ui/c/conv-1')
    await routeReady()
    emit(anEvent.loaded('conv-1'))
    await waitFor(() => expect(pathname()).toBe('/ui/c/conv-1'))

    act(() => {
      store.startNew()
    })

    await waitFor(() => expect(pathname()).toBe('/ui'))
  })

  // FR-MSG-008 / T-096: a recovery state, not an error boundary and not a blank transcript.
  it('offers a way out when the server has never heard of the id in the URL', async () => {
    const { emit } = renderAt('/bedrock-chat/ui/c/ghost')
    await routeReady()

    emit(anEvent.error('conversation_not_found', 'gone', 'ghost'))

    expect(await screen.findByText(CONVERSATION_COPY.unknown.title)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: CONVERSATION_COPY.unknown.action }))

    expect(screen.queryByText(CONVERSATION_COPY.unknown.title)).not.toBeInTheDocument()
  })

  it('leaves an unrelated conversation alone when another id is not found', async () => {
    const { emit } = renderAt('/bedrock-chat/ui/c/conv-1')
    await routeReady()

    emit(anEvent.error('conversation_not_found', 'gone', 'other'))

    expect(screen.queryByText(CONVERSATION_COPY.unknown.title)).not.toBeInTheDocument()
  })
})

describe('pending-turn notice', () => {
  // FR-CONV-009 / FR-CONV-009c.
  it('shows the polling notice and offers a retry only once the attempts are exhausted', async () => {
    const { container, emit, gateway, scheduler, store } = renderAt('/bedrock-chat/ui/c/conv-1')
    await routeReady()
    gateway.calls.length = 0
    await authenticate(container)
    expect(store.getSnapshot().visible).toBe(true)
    await waitFor(() => expect(gateway.calls).toContainEqual(['load', id('conv-1')]))

    emit(anEvent.loaded('conv-1', ['user', 'assistant', 'tool']))

    expect(await screen.findByText(CONVERSATION_COPY.pending.notice)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: CONVERSATION_COPY.pending.retry }),
    ).not.toBeInTheDocument()

    act(() => {
      scheduler.advance(20)
    })

    expect(await screen.findByText(CONVERSATION_COPY.pending.exhausted)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: CONVERSATION_COPY.pending.retry })).toBeVisible()
  })
})
