import { createTokenCount, type TokenCount } from '@/domains/telemetry/domain/public'

import type { TranscriptEntry } from '@/domains/messaging/domain/transcript'
import type { ContextFill, TurnUsage } from '@/domains/messaging/domain/turn'

/** Usage of every answer in the open conversation that reported any. */
export type SessionUsage = {
  readonly tokens: TokenCount
  /** Sum of the priced answers; null when none is priced. */
  readonly estimatedCost: number | null
  readonly currency: string | null
  /** Answers whose usage carries no estimate, so the sum leaves them out. */
  readonly unpricedAnswers: number
  readonly answers: number
  /** Some answer's totals are still pending. */
  readonly pending: boolean
  /** As of the latest answer only: an older reading would be stale once the conversation grew. */
  readonly context: ContextFill | null
}

const usages = (entries: readonly TranscriptEntry[]): TurnUsage[] =>
  entries.flatMap((entry) =>
    entry.message.role === 'assistant' && entry.activity?.usage ? [entry.activity.usage] : [],
  )

const isCompletedAnswer = (entry: TranscriptEntry): boolean =>
  entry.message.role === 'assistant' &&
  !entry.transient &&
  !entry.progress &&
  !entry.pending &&
  !entry.streaming &&
  entry.interrupted === null

export const sessionUsage = (entries: readonly TranscriptEntry[]): SessionUsage | null => {
  const all = usages(entries)
  if (all.length === 0) {
    return null
  }

  // Independent of `all`: a newer answer without usage must not leave an older reading standing.
  const latestAnswer = entries.filter(isCompletedAnswer).at(-1)

  const currency = all.find((usage) => usage.estimatedCost !== null)?.currency ?? null
  // A second currency cannot be added to the first, so it counts as unpriced.
  const priced = all.filter((usage) => usage.estimatedCost !== null && usage.currency === currency)
  const sum = (pick: (tokens: TokenCount) => number): number =>
    all.reduce((total, usage) => total + pick(usage.tokens), 0)

  return {
    tokens: createTokenCount(
      sum((tokens) => tokens.input),
      sum((tokens) => tokens.output),
      sum((tokens) => tokens.cacheRead),
      sum((tokens) => tokens.cacheWrite),
    ),
    estimatedCost: priced.length === 0 ? null : priced.reduce((total, usage) => total + (usage.estimatedCost ?? 0), 0),
    currency,
    unpricedAnswers: all.length - priced.length,
    answers: all.length,
    pending: all.some((usage) => !usage.final),
    context: latestAnswer?.activity?.usage?.context ?? null,
  }
}
