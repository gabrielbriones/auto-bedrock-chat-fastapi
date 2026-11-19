import type { HistoryMessage } from '@/domains/messaging/domain/transcript'
import { NO_ACTIVITY, type Turn, type TurnActivity, type TurnUsage } from '@/domains/messaging/domain/turn'
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

// A loaded answer may have no activity yet: the chat call reported no usage but a later call (the title) did.
export const settleHistoryUsage = (
  history: readonly HistoryMessage[],
  id: string,
  usage: TurnUsage,
): readonly HistoryMessage[] => {
  const settles = (entry: HistoryMessage): boolean =>
    entry.message.id === id && entry.message.role === 'assistant' && entry.interrupted !== true

  return history.some(settles)
    ? history.map((entry) =>
        settles(entry) ? { ...entry, activity: { ...(entry.activity ?? NO_ACTIVITY), usage } } : entry,
      )
    : history
}
