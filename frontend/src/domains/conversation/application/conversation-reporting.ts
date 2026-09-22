import type { ConversationId } from '@/shared/kernel/branded'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import type { Logger } from '@/shared/logging/logger'
import type { NotificationPort } from '@/shared/ports/notification-port'

import { conversationErrorEffect } from '@/domains/conversation/domain/conversation-error'
import type { ConversationEvent } from '@/domains/conversation/domain/events'

export type ErrorOutcome = {
  /** Non-null when the failure belongs next to the roster rather than in a toast (FR-CONV-019). */
  readonly notice: string | null
  readonly disablePersistence: boolean
  readonly refresh: boolean
}

const NO_OUTCOME: ErrorOutcome = { notice: null, disablePersistence: false, refresh: false }

// FR-CONV-019. Decides what the user is told and hands the state consequences back to the store,
// so the store keeps the state and this keeps the wording.
export const reportConversationError = (
  event: Extract<ConversationEvent, { kind: 'error' }>,
  notifications: NotificationPort,
  logger: Logger,
): ErrorOutcome => {
  const effect = conversationErrorEffect(event.code, event.message)
  logger.warn('conversation_error', { code: event.code })

  switch (effect.kind) {
    case 'transient':
      return { ...NO_OUTCOME, notice: effect.message }
    case 'disable-persistence':
      notifications.warning(effect.message)
      return { ...NO_OUTCOME, disablePersistence: true }
    case 'toast-and-refresh':
      notifications.error(effect.message)
      return { ...NO_OUTCOME, refresh: true }
    case 'toast':
      notifications.error(effect.message)
      return NO_OUTCOME
  }
}

// P5: the server may delete fewer ids than were requested. The user is told exactly how many
// survived rather than being shown a success for a partly-failed batch.
export const reportPartialDelete = (
  requested: ReadonlySet<ConversationId> | null,
  deletedIds: readonly ConversationId[],
  notifications: NotificationPort,
  logger: Logger,
): void => {
  if (requested === null) {
    return
  }

  const deleted = new Set(deletedIds)
  const skipped = [...requested].filter((id) => !deleted.has(id))

  if (skipped.length > 0) {
    logger.warn('conversation_bulk_delete_partial', { skipped: skipped.length })
    notifications.error(CONVERSATION_COPY.bulk.partial(skipped.length))
  }
}
