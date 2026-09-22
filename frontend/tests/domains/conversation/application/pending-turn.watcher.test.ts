import { describe, expect, it } from 'vitest'

import { CONVERSATION_COPY } from '@/shared/copy/conversation'

import {
  closedState,
  createHarness,
  openState,
} from './conversation.store.fixture'
import { aConversation, anEvent, id } from '../domain/conversation.fixture'
import {
  PENDING_TURN_INTERVAL_MS,
  PENDING_TURN_MAX_ATTEMPTS,
} from '@/domains/conversation/domain/pending-turn'

const pendingHistory = ['user', 'assistant', 'tool'] as const

// A reply that says the server is still working, exactly as a poll would receive it.
const stillPending = (name: string) => anEvent.loaded(name, pendingHistory)

// The frames that arrive for unrelated reasons while a watch is running. FR-CONV-009a is only
// meaningful if these cannot touch the counter.
const noise = [
  anEvent.titled('other', 'Something else'),
  anEvent.listed([aConversation('a', 0), aConversation('other', 1)]),
  anEvent.renamed('other', 'Renamed'),
  anEvent.error('conversation_history_unavailable', 'flaky'),
]

const startWatching = (harness: ReturnType<typeof createHarness>) => {
  harness.gateway.emit(stillPending('a'))
  harness.gateway.calls.length = 0
}

describe('pending-turn recovery', () => {
  // FR-CONV-009.
  it('starts a 6-second watch when a loaded conversation is still working', () => {
    const harness = createHarness()

    harness.gateway.emit(stillPending('a'))

    expect(harness.scheduler.intervals).toEqual([PENDING_TURN_INTERVAL_MS])
    expect(harness.store.getSnapshot().pendingTurn).toMatchObject({
      conversationId: id('a'),
      attempts: 0,
      exhausted: false,
    })
  })

  it('starts no watch when the conversation has settled', () => {
    const harness = createHarness()

    harness.gateway.emit(anEvent.loaded('a', ['user', 'assistant']))

    expect(harness.store.getSnapshot().pendingTurn).toBeNull()
    expect(harness.scheduler.running()).toBe(false)
  })

  // FR-CONV-009a / Q18. The unrelated frames are the point: a counter that resets on any reply
  // never reaches its bound, which is how the legacy client polled forever.
  it('reaches the bound and stops, with unrelated frames arriving throughout', () => {
    const harness = createHarness()
    startWatching(harness)

    for (let attempt = 0; attempt < PENDING_TURN_MAX_ATTEMPTS; attempt += 1) {
      harness.scheduler.advance()
      harness.gateway.emit(stillPending('a'))
      harness.gateway.emit(noise[attempt % noise.length] ?? noise[0]!)
    }

    const watch = harness.store.getSnapshot().pendingTurn
    expect(watch).toMatchObject({ attempts: PENDING_TURN_MAX_ATTEMPTS, exhausted: true })
    expect(harness.scheduler.running()).toBe(false)
  })

  it('issues one load per attempt, up to and including the last', () => {
    const harness = createHarness()
    startWatching(harness)

    harness.scheduler.advance(PENDING_TURN_MAX_ATTEMPTS)

    const loads = harness.gateway.calls.filter(([method]) => method === 'load')
    expect(loads).toHaveLength(PENDING_TURN_MAX_ATTEMPTS)
  })

  it('does not restart after the reply to its final poll', () => {
    const harness = createHarness()
    startWatching(harness)
    harness.scheduler.advance(PENDING_TURN_MAX_ATTEMPTS)

    harness.gateway.emit(stillPending('a'))

    expect(harness.scheduler.running()).toBe(false)
    expect(harness.store.getSnapshot().pendingTurn?.attempts).toBe(PENDING_TURN_MAX_ATTEMPTS)
  })

  // FR-CONV-009c.
  it('resumes from zero when the user retries after exhaustion', () => {
    const harness = createHarness()
    startWatching(harness)
    harness.scheduler.advance(PENDING_TURN_MAX_ATTEMPTS)

    harness.store.retryPendingTurn()

    expect(harness.store.getSnapshot().pendingTurn).toMatchObject({ attempts: 0, exhausted: false })
    expect(harness.scheduler.running()).toBe(true)
  })

  it('refuses a retry while the socket is closed, with a stated reason', () => {
    const harness = createHarness()
    startWatching(harness)
    harness.scheduler.advance(PENDING_TURN_MAX_ATTEMPTS)
    harness.connection.set(closedState)

    harness.store.retryPendingTurn()

    expect(harness.notifications.messages()).toEqual([CONVERSATION_COPY.offline.load])
  })

  // FR-CONV-009a: a different conversation is the one thing that does reset the counter. It has to
  // be one this client asked for — an unrequested load is a stale reply and is dropped.
  it('restarts the counter when a different conversation is loaded and also pending', () => {
    const harness = createHarness()
    harness.gateway.emit(anEvent.listed([aConversation('a'), aConversation('b')]))
    startWatching(harness)
    harness.scheduler.advance(5)

    harness.store.open(id('b'))
    harness.gateway.emit(stillPending('b'))

    expect(harness.store.getSnapshot().pendingTurn).toMatchObject({
      conversationId: id('b'),
      attempts: 0,
    })
  })

  // FR-CONV-009b.
  it('aborts when the user opens another conversation', () => {
    const harness = createHarness()
    harness.gateway.emit(anEvent.listed([aConversation('a'), aConversation('b')]))
    startWatching(harness)

    harness.store.open(id('b'))

    expect(harness.store.getSnapshot().pendingTurn).toBeNull()
    expect(harness.scheduler.running()).toBe(false)
  })

  it('aborts when the socket closes', () => {
    const harness = createHarness()
    startWatching(harness)

    harness.connection.set(closedState)

    expect(harness.store.getSnapshot().pendingTurn).toBeNull()
    expect(harness.scheduler.running()).toBe(false)
  })

  it('aborts when the watched conversation is deleted', () => {
    const harness = createHarness()
    startWatching(harness)

    harness.gateway.emit(anEvent.deleted('a'))

    expect(harness.store.getSnapshot().pendingTurn).toBeNull()
  })

  it('resolves the watch once the server returns a settled conversation', () => {
    const harness = createHarness()
    startWatching(harness)
    harness.scheduler.advance(3)

    harness.gateway.emit(anEvent.loaded('a', ['user', 'assistant']))

    expect(harness.store.getSnapshot().pendingTurn).toBeNull()
    expect(harness.scheduler.running()).toBe(false)
  })
})

describe('unknown conversation id recovery', () => {
  // FR-MSG-008 / T-096: a defined state, not an error boundary and not a blank transcript.
  it('records the id the server could not find', () => {
    const harness = createHarness()

    harness.gateway.emit(anEvent.error('conversation_not_found', 'gone', 'ghost'))

    expect(harness.store.getSnapshot().unknownId).toBe(id('ghost'))
  })

  it('leaves the recovery state when another conversation loads', () => {
    const harness = createHarness()
    harness.gateway.emit(anEvent.error('conversation_not_found', 'gone', 'ghost'))

    harness.gateway.emit(anEvent.loaded('a'))

    expect(harness.store.getSnapshot().unknownId).toBeNull()
  })

  it('leaves the recovery state when the user dismisses it', () => {
    const harness = createHarness()
    harness.gateway.emit(anEvent.error('conversation_not_found', 'gone', 'ghost'))

    harness.store.dismissUnknownId()

    expect(harness.store.getSnapshot().unknownId).toBeNull()
  })

  // FR-CONV-012 / P4: a reconnect reloads the active conversation without a second copy of it.
  it('reloads the active conversation exactly once after a reconnect', () => {
    const harness = createHarness()
    harness.gateway.emit(anEvent.loaded('a'))
    harness.connection.set(closedState)
    harness.gateway.calls.length = 0

    harness.connection.set(openState)

    expect(harness.gateway.calls.filter(([method]) => method === 'load')).toEqual([
      ['load', id('a')],
    ])
    expect(harness.store.getSnapshot().items.filter((item) => item.id === id('a'))).toHaveLength(1)
  })
})
