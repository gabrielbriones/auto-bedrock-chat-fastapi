import { describe, expect, it } from 'vitest'

import { synchronizeConversationRoute } from '@/app/chat/conversation-route-sync'
import { conversationId } from '@/shared/kernel/branded'

const id = (value: string) => conversationId(value)

const base = {
  routeId: id('a'),
  activeId: null,
  previousActiveId: null,
  requestedRouteId: null,
  available: true,
  awaitingId: false,
}

describe('synchronizeConversationRoute', () => {
  it('loads a direct route once and waits for its response', () => {
    const requested = synchronizeConversationRoute(base)

    expect(requested).toMatchObject({
      requestedRouteId: id('a'),
      command: { kind: 'load', id: id('a') },
    })
    expect(
      synchronizeConversationRoute({ ...base, requestedRouteId: requested.requestedRouteId }).command,
    ).toEqual({ kind: 'none' })
  })

  it('navigates to a conversation selected outside the router', () => {
    const next = synchronizeConversationRoute({
      ...base,
      activeId: id('b'),
      previousActiveId: id('a'),
      requestedRouteId: id('a'),
    })

    expect(next.command).toEqual({ kind: 'navigate', id: id('b') })
    expect(next.requestedRouteId).toBeNull()
  })

  it('writes a selection made from the base route into the URL', () => {
    const next = synchronizeConversationRoute({
      ...base,
      routeId: null,
      activeId: id('a'),
    })

    expect(next.command).toEqual({ kind: 'navigate', id: id('a') })
  })

  it('returns an unsaved conversation to the base route', () => {
    const next = synchronizeConversationRoute({ ...base, awaitingId: true })

    expect(next.command).toEqual({ kind: 'navigate', id: null })
  })

  it('clears route intent while loading is unavailable', () => {
    const next = synchronizeConversationRoute({
      ...base,
      available: false,
      requestedRouteId: id('a'),
    })

    expect(next.command).toEqual({ kind: 'none' })
    expect(next.requestedRouteId).toBeNull()
  })
})
