import fc from 'fast-check'
import { describe, expect, it } from '@jest/globals'

import type { ConversationId } from '@/shared/kernel/branded'
import { isErr, isOk } from '@/shared/kernel/result'

import { aConversation, aRoster, id } from './conversation.fixture'
import { rosterViolations } from '@/domains/conversation/domain/roster-invariants'
import {
  activate,
  awaitFirstTurnId,
  beginBulkDelete,
  clearActive,
  clearItems,
  clearSelection,
  completeBulkDelete,
  emptyRoster,
  recoverActiveAfterRefresh,
  releaseBulkDelete,
  removeMany,
  replaceItems,
  selectAll,
  selectionState,
  toggleSelection,
  upsert,
  type ConversationRoster,
} from '@/domains/conversation/domain/roster'

const NAMES = ['a', 'b', 'c', 'd', 'e'] as const

const anId = fc.constantFrom(...NAMES).map(id)
const anIdSet = fc.uniqueArray(anId, { maxLength: NAMES.length })

// Distinct timestamps are not assumed: colliding ones are exactly what I1's tie-break exists for.
const anItem = fc
  .tuple(fc.constantFrom(...NAMES), fc.integer({ min: -5, max: 5 }))
  .map(([name, offset]) => aConversation(name, offset))

type Operation = (roster: ConversationRoster) => ConversationRoster

// Every mutating operation the aggregate exposes, so the property covers sequences no one would
// think to write by hand — a delete between a select-all and its bulk delete, for instance.
const anOperation: fc.Arbitrary<Operation> = fc.oneof(
  fc
    .uniqueArray(anItem, { selector: (item) => item.id, maxLength: NAMES.length })
    .map((items): Operation => (roster) => replaceItems(roster, items)),
  fc.tuple(anId, anItem).map(
    ([target, item]): Operation =>
      (roster) =>
        upsert(roster, target, { title: item.title, updatedAt: item.updatedAt }, item.updatedAt),
  ),
  anIdSet.map((ids): Operation => (roster) => removeMany(roster, ids)),
  anId.map((target): Operation => (roster) => activate(roster, target)),
  anId.map((target): Operation => (roster) => toggleSelection(roster, target)),
  anIdSet.map(
    (ids): Operation =>
      (roster) => {
        const started = beginBulkDelete(roster, ids)
        return isOk(started) ? started.value : roster
      },
  ),
  anIdSet.map((ids): Operation => (roster) => completeBulkDelete(roster, ids)),
  fc.constant<Operation>(clearActive),
  fc.constant<Operation>(clearItems),
  fc.constant<Operation>(clearSelection),
  fc.constant<Operation>(selectAll),
  fc.constant<Operation>(releaseBulkDelete),
  fc.constant<Operation>(awaitFirstTurnId),
  fc.constant<Operation>(recoverActiveAfterRefresh),
)

const aSequence = fc.array(anOperation, { maxLength: 20 })

describe('ConversationRoster invariants', () => {
  // DESIGN-001 §4.2 I1, I2, I4 (plus id uniqueness) under arbitrary operation sequences.
  it('holds I1, I2 and I4 after every operation in any sequence', () => {
    fc.assert(
      fc.property(aSequence, (operations) => {
        let roster = emptyRoster

        for (const apply of operations) {
          roster = apply(roster)
          expect(rosterViolations(roster)).toEqual([])
        }
      }),
    )
  })

  // I3 is a property of the sequence, not of one state: a second begin must be refused until the
  // first is completed or released.
  it('admits at most one bulk delete in flight (I3)', () => {
    fc.assert(
      fc.property(aSequence, anIdSet, anIdSet, (prefix, first, second) => {
        let roster = emptyRoster
        for (const apply of prefix) {
          roster = apply(roster)
        }

        roster = releaseBulkDelete(roster)
        const started = beginBulkDelete(roster, first)
        expect(isOk(started)).toBe(true)

        const blocked = isOk(started) ? beginBulkDelete(started.value, second) : started
        expect(isErr(blocked)).toBe(true)

        const released = isOk(started) ? releaseBulkDelete(started.value) : roster
        expect(isOk(beginBulkDelete(released, second))).toBe(true)
      }),
    )
  })

  // FR-CONV-007: the selection never outlives the conversations it names.
  it('prunes selection entries whose conversation is gone', () => {
    const roster = selectAll(aRoster([aConversation('a'), aConversation('b'), aConversation('c')]))

    const refreshed = replaceItems(roster, [aConversation('b'), aConversation('c')])

    expect([...refreshed.selection]).toEqual([id('b'), id('c')])
  })
})

describe('ConversationRoster operations', () => {
  it('refuses to activate a conversation it has never listed (I4)', () => {
    expect(activate(emptyRoster, id('a')).activeId).toBeNull()
  })

  it.each([
    ['none', [] as readonly string[]],
    ['partial', ['a']],
    ['all', ['a', 'b']],
  ] as const)('reports a %s selection state for the indeterminate control', (state, selected) => {
    const base = aRoster([aConversation('a'), aConversation('b')])
    const roster = selected.reduce<ConversationRoster>(
      (acc, name) => toggleSelection(acc, id(name)),
      base,
    )

    expect(selectionState(roster)).toBe(state)
  })

  it('reports an empty roster as an unselected one, never as fully selected', () => {
    expect(selectionState(selectAll(emptyRoster))).toBe('none')
  })

  // FR-CONV-006a / P5: the server may delete fewer ids than were requested.
  it('clears the requested set from the selection on a partial bulk delete', () => {
    const roster = selectAll(aRoster([aConversation('a'), aConversation('b'), aConversation('c')]))
    const started = beginBulkDelete(roster, [id('a'), id('b')] as ConversationId[])

    const settled = completeBulkDelete(isOk(started) ? started.value : roster, [id('a')])

    expect(settled.items.map((item) => item.id)).toEqual([id('b'), id('c')])
    expect([...settled.selection]).toEqual([id('c')])
    expect(settled.pendingBulkDelete).toBeNull()
  })

  // FR-CONV-010.
  it('adopts the newest conversation when a refresh follows a turn sent before its id existed', () => {
    const roster = awaitFirstTurnId(emptyRoster)

    const refreshed = recoverActiveAfterRefresh(
      replaceItems(roster, [aConversation('a', 0), aConversation('b', 10)]),
    )

    expect(refreshed.activeId).toBe(id('b'))
    expect(refreshed.awaitingIdForFirstTurn).toBe(false)
  })

  it('does not adopt a conversation when nothing was awaiting an id', () => {
    const refreshed = recoverActiveAfterRefresh(aRoster([aConversation('a')]))

    expect(refreshed.activeId).toBeNull()
  })
})
