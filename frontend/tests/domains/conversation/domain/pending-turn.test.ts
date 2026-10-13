import { describe, expect, it } from '@jest/globals'

import { id } from './conversation.fixture'
import {
  PENDING_TURN_INTERVAL_MS,
  PENDING_TURN_MAX_ATTEMPTS,
  isPendingTurn,
  isPolling,
  isTurnInProgress,
  recordAttempt,
  startWatch,
  watchFor,
} from '@/domains/conversation/domain/pending-turn'

describe('isPendingTurn', () => {
  // P8 / FR-CONV-009.
  it.each([
    ['an empty history', [], false],
    ['a user message last', [{ role: 'user', toolCallCount: 0 }], false],
    ['an answered assistant message last', [{ role: 'assistant', toolCallCount: 0 }], false],
    ['a tool result last', [{ role: 'tool', toolCallCount: 0 }], true],
    ['an assistant message that called tools last', [{ role: 'assistant', toolCallCount: 1 }], true],
  ] as const)('reports %s as pending=%s', (_case, messages, expected) => {
    expect(isPendingTurn(messages)).toBe(expected)
  })

  it('only looks at the last message', () => {
    expect(
      isPendingTurn([
        { role: 'tool', toolCallCount: 0 },
        { role: 'assistant', toolCallCount: 0 },
      ]),
    ).toBe(false)
  })
})

describe('isTurnInProgress', () => {
  const toolLast = [{ role: 'tool', toolCallCount: 0 }] as const
  const answered = [{ role: 'assistant', toolCallCount: 0 }] as const

  // XMGPLAT-11977: an interrupted turn ends on a tool result but nothing is running.
  it('trusts the server over a tool-terminated history', () => {
    expect(isTurnInProgress(false, toolLast)).toBe(false)
  })

  it('trusts the server over an answered history', () => {
    expect(isTurnInProgress(true, answered)).toBe(true)
  })

  it('falls back to the history shape when the server did not say', () => {
    expect(isTurnInProgress(null, toolLast)).toBe(true)
    expect(isTurnInProgress(null, answered)).toBe(false)
  })
})

describe('PendingTurnWatch', () => {
  it('polls every 6 seconds for at most 20 attempts', () => {
    expect(PENDING_TURN_INTERVAL_MS).toBe(6_000)
    expect(PENDING_TURN_MAX_ATTEMPTS).toBe(20)
  })

  // FR-CONV-009a / Q18: this is the invariant the legacy client broke.
  it('keeps the counter when the watched conversation is unchanged', () => {
    const watch = recordAttempt(recordAttempt(startWatch(id('a'))))

    expect(watchFor(watch, id('a')).attempts).toBe(2)
  })

  it('resets the counter only when the conversation changes', () => {
    const watch = recordAttempt(startWatch(id('a')))

    expect(watchFor(watch, id('b')).attempts).toBe(0)
  })

  // FR-CONV-009c.
  it('exhausts exactly at the ceiling and stops polling', () => {
    let watch = startWatch(id('a'))

    for (let attempt = 0; attempt < PENDING_TURN_MAX_ATTEMPTS - 1; attempt += 1) {
      watch = recordAttempt(watch)
      expect(isPolling(watch)).toBe(true)
    }

    watch = recordAttempt(watch)

    expect(watch.attempts).toBe(PENDING_TURN_MAX_ATTEMPTS)
    expect(watch.exhausted).toBe(true)
    expect(isPolling(watch)).toBe(false)
  })

  it('reports no watch as not polling', () => {
    expect(isPolling(null)).toBe(false)
  })
})
