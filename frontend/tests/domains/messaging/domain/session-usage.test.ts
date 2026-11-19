import { describe, expect, it } from '@jest/globals'

import { Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

import { createMessage, type MessageRole } from '@/domains/messaging/domain/message'
import { sessionUsage } from '@/domains/messaging/domain/session-usage'
import type { TranscriptEntry } from '@/domains/messaging/domain/transcript'
import type { TurnUsage } from '@/domains/messaging/domain/turn'
import { createTokenCount } from '@/domains/telemetry/domain/public'

const epoch = Instant.fromEpochMilliseconds(0)
if (!isOk(epoch)) {
  throw new Error('Expected test instant to be valid')
}

const usage = (overrides: Partial<TurnUsage> = {}): TurnUsage => ({
  tokens: createTokenCount(1_000, 100, 400, 10),
  byPurpose: [],
  estimatedCost: 0.01,
  currency: 'USD',
  final: true,
  context: null,
  ...overrides,
})

const entry = (value: TurnUsage | null, role: MessageRole = 'assistant', seq = 1): TranscriptEntry => ({
  key: `entry-${seq}`,
  message: createMessage(role, 'text', epoch.value, seq),
  pending: false,
  streaming: false,
  transient: false,
  progress: false,
  interrupted: null,
  activity: value === null ? null : { toolCalls: [], toolResults: [], citations: [], truncated: false, usage: value },
})

describe('sessionUsage', () => {
  it('is null until an answer reports usage', () => {
    expect(sessionUsage([])).toBeNull()
    expect(sessionUsage([entry(null, 'user'), entry(null, 'assistant', 2)])).toBeNull()
  })

  it('sums tokens and estimates over every answer, ignoring user entries', () => {
    const result = sessionUsage([
      entry(usage(), 'assistant', 1),
      entry(usage({ estimatedCost: 0.02 }), 'assistant', 2),
      entry(usage(), 'user', 3),
    ])

    expect(result).toMatchObject({
      tokens: { input: 2_000, output: 200, cacheRead: 800, cacheWrite: 20, total: 2_200 },
      currency: 'USD',
      unpricedAnswers: 0,
      answers: 2,
      pending: false,
    })
    expect(result?.estimatedCost).toBeCloseTo(0.03)
  })

  it('counts unpriced answers and answers in another currency apart from the sum', () => {
    const result = sessionUsage([
      entry(usage({ estimatedCost: null, currency: null }), 'assistant', 1),
      entry(usage(), 'assistant', 2),
      entry(usage({ estimatedCost: 5, currency: 'EUR' }), 'assistant', 3),
    ])

    expect(result).toMatchObject({ estimatedCost: 0.01, currency: 'USD', unpricedAnswers: 2, answers: 3 })
  })

  it('has no estimate when no answer is priced', () => {
    expect(sessionUsage([entry(usage({ estimatedCost: null, currency: null }))])).toMatchObject({
      estimatedCost: null,
      unpricedAnswers: 1,
    })
  })

  it('takes the context fill from the latest answer only, and flags pending totals', () => {
    const older = usage({ context: { usedTokens: 100, maxTokens: 1_000 } })
    const latest = usage({ context: null, final: false })

    expect(sessionUsage([entry(older, 'assistant', 1)])?.context).toEqual({ usedTokens: 100, maxTokens: 1_000 })
    expect(sessionUsage([entry(older, 'assistant', 1), entry(latest, 'assistant', 2)])).toMatchObject({
      context: null,
      pending: true,
    })
  })
})
