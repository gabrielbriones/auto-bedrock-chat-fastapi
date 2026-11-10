import { messageId } from '@/shared/kernel/branded'

import { createMessage } from '@/domains/messaging/domain/message'
import type { HistoryMessage } from '@/domains/messaging/domain/transcript'
import type { MessagingEvent } from '@/domains/messaging/application/ports'

// A loaded conversation as transcript history: sequence numbers follow the server's order.
export const toHistory = (event: Extract<MessagingEvent, { kind: 'history-loaded' }>): HistoryMessage[] =>
  event.messages.map((message, index) => ({
    message: createMessage(
      message.role,
      message.text,
      message.at,
      index + 1,
      message.id === null ? null : messageId(message.id),
    ),
    activity: message.activity,
    interrupted: message.interrupted === true,
    conversationId: event.conversationId,
  }))
