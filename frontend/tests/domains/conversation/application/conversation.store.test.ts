import { describe, expect, it } from '@jest/globals'

import { CONVERSATION_COPY } from '@/shared/copy/conversation'

import { createHarness, openState, closedState } from './conversation.store.fixture'
import { aConversation, anEvent, id } from '../domain/conversation.fixture'

type Store = ReturnType<typeof createHarness>['store']

describe('ConversationStore visibility', () => {
  // FR-CONV-001.
  it('stays hidden while persistence is disabled by the server config', () => {
    const { store } = createHarness({ persistenceEnabled: false })

    expect(store.getSnapshot().visible).toBe(false)
  })

  it('stays hidden until a principal is authenticated', () => {
    const { store, gateway } = createHarness({ authenticated: false })

    expect(store.getSnapshot().visible).toBe(false)
    expect(gateway.methods()).not.toContain('requestRoster')
  })

  it('requests the roster on becoming visible', () => {
    const { store, gateway } = createHarness({ authenticated: false })

    store.setAuthenticated(true)

    expect(gateway.methods()).toEqual(['requestRoster'])
    expect(store.getSnapshot().visible).toBe(true)
  })

  it('clears the active conversation on becoming hidden', () => {
    const { store, gateway } = createHarness()
    gateway.emit(anEvent.loaded('a'))

    store.setAuthenticated(false)

    expect(store.getSnapshot().activeId).toBeNull()
  })

  // FR-CONV-012 / P2 / P4.
  it('re-requests the roster and reloads the active conversation after a reconnect', () => {
    const { gateway, connection } = createHarness()
    gateway.emit(anEvent.loaded('a'))
    connection.set(closedState)
    gateway.calls.length = 0

    connection.set(openState)

    expect(gateway.calls).toEqual([
      ['requestRoster', undefined],
      ['load', id('a')],
    ])
  })
})

describe('ConversationStore mutations', () => {
  it('ignores a late load response after another conversation was requested or a new one is started', () => {
    const { store, gateway } = createHarness()
    gateway.emit(anEvent.listed([aConversation('a'), aConversation('b', 5)]))
    store.open(id('a'))
    store.open(id('b'))

    gateway.emit(anEvent.loaded('b'))
    gateway.emit(anEvent.loaded('a'))

    expect(store.getSnapshot().activeId).toBe(id('b'))

    store.startNew()
    gateway.emit(anEvent.loaded('b'))

    expect(store.getSnapshot().activeId).toBeNull()
  })

  // FR-CONV-002 / P3.
  it('starts a new conversation and waits for the id the first turn will produce', () => {
    const { store, gateway } = createHarness()

    store.startNew()

    expect(gateway.methods()).toEqual(['create'])
    expect(store.getSnapshot().activeId).toBeNull()
  })

  // FR-CONV-004 / FIX-15: the shared prompt is asked, pre-filled, and the frame carries the trim.
  it('renames through the confirmation port', async () => {
    const { store, gateway, confirmations } = createHarness({ answers: ['  Job 42  '] })
    gateway.emit(anEvent.listed([aConversation('a', 0, 'Old')]))

    await store.rename(id('a'))

    expect(confirmations.asked[0]).toMatchObject({ initialValue: 'Old' })
    expect(gateway.calls.at(-1)).toEqual(['rename', id('a'), 'Job 42'])
  })

  it.each<[string, boolean | null, (store: Store) => Promise<void>]>([
    ['rename', null, (store) => store.rename(id('a'))],
    ['remove', false, (store) => store.remove(id('a'))],
    ['removeMany', false, (store) => {
      store.toggleSelectAll()
      return store.removeSelected()
    }],
  ])('makes no %s request when the confirmation is declined', async (method, answer, act) => {
    const { store, gateway, confirmations } = createHarness({ answers: [answer] })
    gateway.emit(anEvent.listed([aConversation('a')]))

    await act(store)

    expect(confirmations.asked).toHaveLength(1)
    expect(gateway.methods()).not.toContain(method)
  })

  it('rejects a blank rename with a field error and makes no request', async () => {
    const { store, gateway, notifications } = createHarness({ answers: ['   '] })
    gateway.emit(anEvent.listed([aConversation('a')]))

    await store.rename(id('a'))

    expect(notifications.messages()).toEqual([CONVERSATION_COPY.rename.empty])
    expect(gateway.methods()).not.toContain('rename')
  })

  // FR-CONV-005: the confirmation names the conversation, and the fallback title is used untitled.
  it('names the conversation in the delete confirmation', async () => {
    const { store, gateway, confirmations } = createHarness({ answers: [true] })
    gateway.emit(anEvent.listed([aConversation('a')]))

    await store.remove(id('a'))

    expect(confirmations.asked[0]).toMatchObject({
      message: CONVERSATION_COPY.delete.message(CONVERSATION_COPY.untitled),
      tone: 'destructive',
    })
    expect(gateway.calls.at(-1)).toEqual(['remove', id('a')])
  })

  // FR-CONV-016 / FIX-16: the list and its total come from the server, not an in-place mutation.
  it('refetches the roster after a deletion', () => {
    const { gateway } = createHarness()
    gateway.emit(anEvent.listed([aConversation('a'), aConversation('b', 5)]))
    gateway.calls.length = 0

    gateway.emit(anEvent.deleted('a'))

    expect(gateway.methods()).toEqual(['requestRoster'])
  })
})

describe('ConversationStore bulk delete', () => {
  const seed = (harness: ReturnType<typeof createHarness>) => {
    harness.gateway.emit(
      anEvent.listed([aConversation('a', 0), aConversation('b', 5), aConversation('c', 9)]),
    )
    harness.store.toggleSelectAll()
    harness.gateway.calls.length = 0
  }

  // FR-CONV-006.
  it('confirms with the selection count before deleting', async () => {
    const harness = createHarness({ answers: [true] })
    seed(harness)

    await harness.store.removeSelected()

    expect(harness.confirmations.asked[0]).toMatchObject({
      title: CONVERSATION_COPY.bulk.title(3),
    })
    expect(harness.gateway.calls).toEqual([['removeMany', [id('c'), id('b'), id('a')]]])
  })

  // FR-CONV-006 / I3.
  it('refuses a second bulk delete while one is in flight', async () => {
    const harness = createHarness({ answers: [true] })
    seed(harness)
    await harness.store.removeSelected()

    await harness.store.removeSelected()

    expect(harness.gateway.methods()).toEqual(['removeMany'])
    expect(harness.notifications.messages()).toEqual([CONVERSATION_COPY.bulk.inFlight])
  })

  // P5 / FR-CONV-006a: a partly-failed batch is reported, not shown as a success.
  it('reports exactly how many ids the server did not delete, and refetches', async () => {
    const harness = createHarness({ answers: [true] })
    seed(harness)
    await harness.store.removeSelected()
    harness.gateway.calls.length = 0

    harness.gateway.emit(anEvent.bulkDeleted(['a']))

    expect(harness.notifications.messages()).toEqual([CONVERSATION_COPY.bulk.partial(2)])
    expect(harness.gateway.methods()).toEqual(['requestRoster'])
    expect(harness.store.getSnapshot().selection.size).toBe(0)
    expect(harness.store.getSnapshot().bulkDeleteInFlight).toBe(false)
  })

  it('says nothing when every requested id was deleted', async () => {
    const harness = createHarness({ answers: [true] })
    seed(harness)
    await harness.store.removeSelected()

    harness.gateway.emit(anEvent.bulkDeleted(['a', 'b', 'c']))

    expect(harness.notifications.messages()).toEqual([])
  })
})

describe('ConversationStore offline and error handling', () => {
  // FR-CONV-017 / ADR-012.
  it.each([
    ['startNew', CONVERSATION_COPY.offline.create],
    ['open', CONVERSATION_COPY.offline.load],
    ['refresh', CONVERSATION_COPY.offline.refresh],
  ] as const)('refuses %s while the socket is closed, with a stated reason', (method, message) => {
    const { store, gateway, notifications } = createHarness({ connected: false })

    if (method === 'open') {
      store.open(id('a'))
    } else {
      store[method]()
    }

    expect(gateway.calls).toEqual([])
    expect(notifications.messages()).toEqual([message])
  })

  it('refuses a confirmed delete while the socket is closed, without mutating the roster', async () => {
    const { store, gateway, notifications } = createHarness({
      connected: false,
      answers: [true],
    })
    gateway.emit(anEvent.listed([aConversation('a')]))

    await store.remove(id('a'))

    expect(gateway.methods()).not.toContain('remove')
    expect(notifications.messages()).toEqual([CONVERSATION_COPY.offline.delete])
    expect(store.getSnapshot().items).toHaveLength(1)
  })

  // FR-CONV-019.
  it('shows an unavailable-history error in place rather than as a toast', () => {
    const { store, gateway, notifications } = createHarness()

    gateway.emit(anEvent.error('conversation_history_unavailable', 'History is down'))

    expect(store.getSnapshot().notice).toBe(CONVERSATION_COPY.errors.transient('History is down'))
    expect(notifications.messages()).toEqual([])
  })

  it('toasts and refreshes when the server has never heard of the conversation', () => {
    const { gateway, notifications } = createHarness()
    gateway.calls.length = 0

    gateway.emit(anEvent.error('conversation_not_found'))

    expect(notifications.messages()).toEqual([CONVERSATION_COPY.errors.notFound])
    expect(gateway.methods()).toContain('requestRoster')
  })

  it('hides the sidebar for the session when persistence is switched off server-side', () => {
    const { store, gateway } = createHarness()

    gateway.emit(anEvent.error('conversation_persistence_disabled'))

    expect(store.getSnapshot().visible).toBe(false)
  })

  it('falls back to the frame message for a code it does not know', () => {
    const { gateway, notifications } = createHarness()

    gateway.emit(anEvent.error('something_new', 'Something new went wrong'))

    expect(notifications.messages()).toEqual(['Something new went wrong'])
  })
})
