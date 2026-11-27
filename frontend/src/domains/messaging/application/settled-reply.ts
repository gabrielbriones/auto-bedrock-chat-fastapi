import type { ConversationId } from '@/shared/kernel/branded'

import type { Turn } from '@/domains/messaging/domain/turn'

/** FR-MSG-032: a turn this session sent has just resolved with a reply the user can read. */
export type SettledReply = {
  readonly outcome: 'answered' | 'failed'
  readonly text: string
  readonly conversationId: ConversationId | null
}

/** The reply a just-resolved turn settled with; `null` unless it was answered or failed. */
export const settledReplyOf = (turn: Turn | undefined): SettledReply | null =>
  turn === undefined || turn.response === null || (turn.status !== 'answered' && turn.status !== 'failed')
    ? null
    : { outcome: turn.status, text: turn.response.raw, conversationId: turn.conversationId }
