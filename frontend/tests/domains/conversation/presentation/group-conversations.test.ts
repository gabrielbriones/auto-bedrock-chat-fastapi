import { describe, expect, it } from '@jest/globals'

import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import { createConversation } from '@/domains/conversation/domain/conversation'
import { groupConversations } from '@/domains/conversation/presentation/group-conversations'
import { Instant } from '@/shared/kernel/instant'
import { id } from '../domain/conversation.fixture'

const localDate = (daysAgo: number): number =>
  new Date(2026, 6, 15 - daysAgo, 12).getTime()

const conversationAt = (name: string, daysAgo: number, title: string) => {
  const timestamp = Instant.fromEpochMilliseconds(localDate(daysAgo))
  if (timestamp.kind === 'err') throw new Error('invalid test timestamp')
  return createConversation(id(name), timestamp.value, title)
}

describe('groupConversations', () => {
  const now = new Date(localDate(0))
  const items = [
    conversationAt('today', 0, 'Build report'),
    conversationAt('yesterday', 1, 'Incident notes'),
    conversationAt('week', 7, 'Weekly summary'),
    conversationAt('older', 8, 'Old report'),
  ]

  it('groups by local calendar day into the four timeline sections', () => {
    expect(groupConversations(items, now).map(({ label, conversations }) => [
      label,
      conversations.map(({ id }) => id),
    ])).toEqual([
      [CONVERSATION_COPY.sidebar.groups.today, ['today']],
      [CONVERSATION_COPY.sidebar.groups.yesterday, ['yesterday']],
      [CONVERSATION_COPY.sidebar.groups.previous7Days, ['week']],
      [CONVERSATION_COPY.sidebar.groups.older, ['older']],
    ])
  })

  it('omits empty sections', () => {
    expect(groupConversations([items[0]!, items[3]!], now).map(({ label }) => label)).toEqual([
      CONVERSATION_COPY.sidebar.groups.today,
      CONVERSATION_COPY.sidebar.groups.older,
    ])
  })

  it('returns nothing for an empty roster', () => {
    expect(groupConversations([], now)).toEqual([])
  })
})
