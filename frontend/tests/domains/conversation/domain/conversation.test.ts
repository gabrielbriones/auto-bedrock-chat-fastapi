import { describe, expect, it } from '@jest/globals'

import { isErr, isOk } from '@/shared/kernel/result'

import {
  byRecencyThenId,
  createConversation,
  mergeConversation,
  renameConversation,
} from '@/domains/conversation/domain/conversation'
import { aConversation, at, id } from './conversation.fixture'

describe('Conversation', () => {
  it('starts untitled with no messages', () => {
    const conversation = createConversation(id('a'), at(0))

    expect(conversation.title).toBeNull()
    expect(conversation.messageCount).toBe(0)
  })

  // I3: a negative or fractional count can only come from a malformed payload.
  it.each([-1, Number.NaN, Number.POSITIVE_INFINITY, 2.7])(
    'clamps a message count of %s to a non-negative integer (I3)',
    (count) => {
      expect(createConversation(id('a'), at(0), null, count).messageCount).toBeGreaterThanOrEqual(0)
      expect(Number.isInteger(createConversation(id('a'), at(0), null, count).messageCount)).toBe(
        true,
      )
    },
  )

  // I2.
  it.each(['', '   ', '\n\t'])('rejects a rename to %j (I2)', (title) => {
    expect(isErr(renameConversation(aConversation('a'), title, at(1)))).toBe(true)
  })

  it('trims an accepted rename and moves the conversation forward in time (I2)', () => {
    const result = renameConversation(aConversation('a'), '  Job 42  ', at(5))

    expect(isOk(result) && result.value.title).toBe('Job 42')
    expect(isOk(result) && result.value.updatedAt.equals(at(5))).toBe(true)
  })

  // I1.
  it('never takes an id from a merge (I1)', () => {
    const merged = mergeConversation(aConversation('a'), { title: 'renamed' })

    expect(merged.id).toBe(id('a'))
  })

  // FR-CONV-008: a frame that omits the title must not blank one the roster already has.
  it('keeps an existing title when the patch omits one', () => {
    const titled = aConversation('a', 0, 'Job 42')

    expect(mergeConversation(titled, { messageCount: 3 }).title).toBe('Job 42')
  })

  // FR-CONV-013.
  it('orders newest first, breaking ties on id so the sort is stable', () => {
    const older = aConversation('a', 0)
    const newer = aConversation('b', 10)

    expect(byRecencyThenId(newer, older)).toBeLessThan(0)
    expect(byRecencyThenId(aConversation('a', 0), aConversation('b', 0))).toBeLessThan(0)
  })
})
