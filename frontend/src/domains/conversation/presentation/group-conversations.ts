import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import type { Conversation } from '@/domains/conversation/domain/conversation'

export type ConversationGroup = {
  readonly label: string
  readonly conversations: readonly Conversation[]
}

const DAY_MS = 24 * 60 * 60 * 1000

// Local calendar day, so a chat from 23:59 yesterday is "Yesterday" and not "Today".
const calendarDay = (date: Date): number =>
  Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY_MS)

const labelFor = (daysAgo: number): string => {
  const { groups } = CONVERSATION_COPY.sidebar
  if (daysAgo <= 0) return groups.today
  if (daysAgo === 1) return groups.yesterday
  if (daysAgo <= 7) return groups.previous7Days
  return groups.older
}

export const groupConversations = (
  items: readonly Conversation[],
  now: Date,
): readonly ConversationGroup[] => {
  const today = calendarDay(now)
  const { groups } = CONVERSATION_COPY.sidebar
  const buckets = new Map<string, Conversation[]>(
    [groups.today, groups.yesterday, groups.previous7Days, groups.older].map((label) => [label, []]),
  )

  for (const conversation of items) {
    const daysAgo = today - calendarDay(new Date(conversation.updatedAt.epochMilliseconds))
    buckets.get(labelFor(daysAgo))?.push(conversation)
  }

  return [...buckets].flatMap(([label, conversations]) =>
    conversations.length > 0 ? [{ label, conversations }] : [],
  )
}
