import type { HistoryMessage } from '@/domains/messaging/domain/transcript'
import type { Turn, TurnActivity, TurnUsage } from '@/domains/messaging/domain/turn'
import type { MessagingEvent } from '@/domains/messaging/application/ports'

export const answeredActivity = (event: Extract<MessagingEvent, { kind: 'answered' }>): TurnActivity => ({
  toolCalls: event.toolCalls,
  toolResults: event.toolResults,
  citations: event.citations,
  truncated: event.truncated,
  usage: event.usage,
})

// `usage_updated` settles an answer after its turn resolved, so it patches rather than transitions.
// The answer may also be loaded history, when the conversation was reopened before the final totals.
export const settleTurnUsage = (turns: readonly Turn[], id: string, usage: TurnUsage): readonly Turn[] =>
  turns.some((turn) => turn.response?.id === id)
    ? turns.map((turn) => (turn.response?.id === id ? { ...turn, activity: { ...turn.activity, usage } } : turn))
    : turns

export const settleHistoryUsage = (
  history: readonly HistoryMessage[],
  id: string,
  usage: TurnUsage,
): readonly HistoryMessage[] =>
  history.some((entry) => entry.message.id === id && entry.activity !== null)
    ? history.map((entry) =>
        entry.message.id === id && entry.activity !== null ? { ...entry, activity: { ...entry.activity, usage } } : entry,
      )
    : history
