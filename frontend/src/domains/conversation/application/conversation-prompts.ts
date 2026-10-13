import type { ConfirmationPort } from '@/shared/ports/confirmation-port'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'

/** `null` is a cancel; `'empty'` is a submitted blank, which FR-CONV-004 rejects without a request. */
export type RenameAnswer = string | null | 'empty'

// FIX-15 / FR-CONV-004: every question the conversation use cases ask goes through the shared
// confirmation port. Gathered here so the store reads as decisions rather than as dialog copy.
export const askForNewTitle = async (
  confirmations: ConfirmationPort,
  currentTitle: string | null,
): Promise<RenameAnswer> => {
  const submitted = await confirmations.prompt({
    title: CONVERSATION_COPY.rename.title,
    label: CONVERSATION_COPY.rename.label,
    initialValue: currentTitle ?? '',
    confirmLabel: CONVERSATION_COPY.rename.confirm,
  })

  if (submitted === null) {
    return null
  }

  return submitted.trim() === '' ? 'empty' : submitted.trim()
}

// FR-CONV-005: the confirmation names the conversation, so a mis-click is visible before it lands.
export const confirmDelete = (
  confirmations: ConfirmationPort,
  title: string | null,
): Promise<boolean> =>
  confirmations.confirm({
    title: CONVERSATION_COPY.delete.title,
    message: CONVERSATION_COPY.delete.message(title ?? CONVERSATION_COPY.untitled),
    confirmLabel: CONVERSATION_COPY.delete.confirm,
    tone: 'destructive',
  })

// FR-CONV-006.
export const confirmBulkDelete = (
  confirmations: ConfirmationPort,
  count: number,
): Promise<boolean> =>
  confirmations.confirm({
    title: CONVERSATION_COPY.bulk.title(count),
    message: CONVERSATION_COPY.bulk.message,
    confirmLabel: CONVERSATION_COPY.bulk.confirm,
    tone: 'destructive',
  })
