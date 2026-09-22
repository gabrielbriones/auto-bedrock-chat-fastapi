import fc from 'fast-check'
import { describe, expect, it, vi } from 'vitest'

import { isOk } from '@/shared/kernel/result'

import {
  aConversation,
  anEvent,
  aRoster,
  at,
  id,
} from './conversation.fixture'
import type { ConversationEvent } from '@/domains/conversation/domain/events'
import { reconcileRoster } from '@/domains/conversation/domain/reconcile-roster'
import { rosterViolations } from '@/domains/conversation/domain/roster-invariants'
import {
  awaitFirstTurnId,
  beginBulkDelete,
  clearSelection,
  emptyRoster,
  selectAll,
  toggleSelection,
  type ConversationRoster,
} from '@/domains/conversation/domain/roster'

const NOW = at(100)

const reduce = (roster: ConversationRoster, ...events: readonly ConversationEvent[]) =>
  events.reduce((acc, event) => reconcileRoster(acc, event, NOW), roster)

// One per `conv`-owned frame (CONTRACT-001 §2.3). The count is asserted below, so a tenth frame
// cannot be added without a case here.
const EVERY_EVENT: readonly ConversationEvent[] = [
  anEvent.created('a'),
  anEvent.titled('a', 'Job 42'),
  anEvent.listed([aConversation('a', 0), aConversation('b', 5)]),
  anEvent.loaded('a', ['user', 'assistant']),
  anEvent.renamed('a', 'Renamed'),
  anEvent.deleted('a'),
  anEvent.bulkDeleted(['a', 'b']),
  anEvent.allDeleted(2),
  anEvent.error('conversation_not_found'),
]

const aSeededRoster = () =>
  selectAll(aRoster([aConversation('a', 0, 'Job 42'), aConversation('b', 5), aConversation('c', 9)]))

describe('reconcileRoster', () => {
  it('handles all nine conversation frames', () => {
    const kinds = new Set(EVERY_EVENT.map((event) => event.kind))

    expect(kinds.size).toBe(9)
  })

  // The reducer is the only place roster state is decided, so it must never break the aggregate.
  it('leaves the roster invariant-clean for every event', () => {
    for (const event of EVERY_EVENT) {
      expect(rosterViolations(reduce(aSeededRoster(), event))).toEqual([])
    }
  })

  it('is idempotent: applying the same frame twice changes nothing more', () => {
    for (const event of EVERY_EVENT) {
      const once = reduce(aSeededRoster(), event)

      expect(reduce(once, event)).toEqual(once)
    }
  })

  // Pure: `at` is the only source of time. A reducer that read the clock would make the same
  // sequence reconcile differently on every run.
  it('never reads the wall clock', () => {
    const now = vi.spyOn(Date, 'now')

    for (const event of EVERY_EVENT) {
      reduce(aSeededRoster(), event)
    }

    expect(now).not.toHaveBeenCalled()
    now.mockRestore()
  })

  it('reconciles the same sequence to the same roster every time', () => {
    fc.assert(
      fc.property(fc.shuffledSubarray(EVERY_EVENT.slice(), { minLength: 2 }), (events) => {
        expect(reduce(emptyRoster, ...events)).toEqual(reduce(emptyRoster, ...events))
      }),
    )
  })
})

describe('reconcileRoster frame handling', () => {
  // FR-CONV-008: an unknown id is inserted at the head rather than dropped.
  it('inserts an unknown conversation and makes it active on created', () => {
    const roster = reduce(emptyRoster, anEvent.created('new'))

    expect(roster.items.map((item) => item.id)).toEqual([id('new')])
    expect(roster.activeId).toBe(id('new'))
  })

  // FR-CONV-008: order between `titled` and `created` is not guaranteed.
  it('converges whether titled arrives before or after created', () => {
    const titledFirst = reduce(emptyRoster, anEvent.titled('a', 'Job 42'), anEvent.created('a'))
    const createdFirst = reduce(emptyRoster, anEvent.created('a'), anEvent.titled('a', 'Job 42'))

    expect(titledFirst).toEqual(createdFirst)
    expect(titledFirst.items[0]?.title).toBe('Job 42')
  })

  // FR-CONV-016: a single page, so a list frame replaces rather than merges.
  it('replaces the roster on a list frame and prunes what it dropped', () => {
    const roster = reduce(
      aSeededRoster(),
      anEvent.listed([aConversation('b', 5), aConversation('c', 9)]),
    )

    expect(roster.items.map((item) => item.id)).toEqual([id('c'), id('b')])
    expect([...roster.selection]).toEqual([id('c'), id('b')])
  })

  // FR-CONV-010.
  it('adopts the newest conversation when a list follows a turn sent before its id existed', () => {
    const roster = reduce(
      awaitFirstTurnId(emptyRoster),
      anEvent.listed([aConversation('a', 0), aConversation('b', 5)]),
    )

    expect(roster.activeId).toBe(id('b'))
  })

  it('activates a loaded conversation it had not listed yet', () => {
    expect(reduce(emptyRoster, anEvent.loaded('deep-link')).activeId).toBe(id('deep-link'))
  })

  // I4: deleting the active conversation cannot leave the roster pointing at nothing.
  it('clears the active conversation when it is deleted', () => {
    const active = reduce(aSeededRoster(), anEvent.loaded('a'))

    expect(reduce(active, anEvent.deleted('a')).activeId).toBeNull()
  })

  // FR-CONV-006a / P5.
  it('drops the whole requested set from the selection on a partial bulk delete', () => {
    const started = beginBulkDelete(aSeededRoster(), [id('a'), id('b')])
    const roster = reduce(isOk(started) ? started.value : emptyRoster, anEvent.bulkDeleted(['a']))

    expect(roster.items.map((item) => item.id)).toEqual([id('c'), id('b')])
    expect([...roster.selection]).toEqual([id('c')])
    expect(roster.pendingBulkDelete).toBeNull()
  })

  // FR-CONV-006b.
  it('clears the active conversation when the server says it was bulk deleted', () => {
    const active = reduce(aSeededRoster(), anEvent.loaded('c'))

    expect(reduce(active, anEvent.bulkDeleted(['a'], true)).activeId).toBeNull()
  })

  // FR-CONV-018.
  it('empties the roster, the selection and the active conversation on all-deleted', () => {
    const roster = reduce(aSeededRoster(), anEvent.loaded('a'), anEvent.allDeleted(3))

    expect(roster.items).toEqual([])
    expect(roster.selection.size).toBe(0)
    expect(roster.activeId).toBeNull()
  })

  // FR-CONV-019: a failed bulk delete must not block every later one.
  it('releases the bulk-delete guard on an error frame', () => {
    const started = beginBulkDelete(aSeededRoster(), [id('a')])
    const roster = reduce(
      isOk(started) ? started.value : emptyRoster,
      anEvent.error('conversation_history_unavailable'),
    )

    expect(roster.pendingBulkDelete).toBeNull()
    expect(roster.items).toHaveLength(3)
  })

  it('leaves an unrelated selection untouched when a conversation is renamed', () => {
    const selected = toggleSelection(clearSelection(aSeededRoster()), id('b'))

    const roster = reduce(selected, anEvent.renamed('a', 'Renamed'))

    expect(roster.selection.has(id('b'))).toBe(true)
    expect(roster.items.find((item) => item.id === id('a'))?.title).toBe('Renamed')
  })
})
