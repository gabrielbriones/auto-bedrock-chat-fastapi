import { useMemo } from 'react'

import { useContainer } from '@/app/bootstrap/container-context'
import type { ConversationId } from '@/shared/kernel/branded'

export type ConversationActions = {
  readonly startNew: () => void
  readonly open: (id: ConversationId) => void
  readonly rename: (id: ConversationId) => void
  readonly remove: (id: ConversationId) => void
  readonly removeSelected: () => void
  readonly toggleSelected: (id: ConversationId) => void
  readonly toggleSelectAll: () => void
  readonly clearSelected: () => void
}

export type ConversationActionOptions = {
  /** FR-CONV-003: the below-breakpoint drawer closes after anything that changes what is shown. */
  readonly onNavigate?: () => void
}

// The store's use cases as event handlers: the async ones are deliberately voided here, in one
// place, rather than in every JSX callback that invokes them.
export const useConversationActions = ({
  onNavigate,
}: ConversationActionOptions = {}): ConversationActions => {
  const { conversations } = useContainer()

  return useMemo(
    () => ({
      startNew: () => {
        conversations.startNew()
        onNavigate?.()
      },
      open: (id: ConversationId) => {
        conversations.open(id)
        onNavigate?.()
      },
      rename: (id: ConversationId) => {
        void conversations.rename(id)
      },
      remove: (id: ConversationId) => {
        void conversations.remove(id)
      },
      removeSelected: () => {
        void conversations.removeSelected()
      },
      toggleSelected: (id: ConversationId) => {
        conversations.toggleSelected(id)
      },
      toggleSelectAll: () => {
        conversations.toggleSelectAll()
      },
      clearSelected: () => {
        conversations.clearSelected()
      },
    }),
    [conversations, onNavigate],
  )
}
