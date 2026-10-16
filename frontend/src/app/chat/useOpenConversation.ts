import { useCallback } from 'react'
import { useNavigate, useParams } from '@tanstack/react-router'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import type { ConversationId } from '@/shared/kernel/branded'

// FR-CONV-003 / FR-CONV-011. A sidebar pick moves the URL straight away and the route asks the
// store to load it, so the address bar never waits on the server's reply (XMGPLAT-11999).
export const useOpenConversation = (): ((id: ConversationId) => void) => {
  const { conversations } = useContainer()
  const { activeId } = useContainerStore('conversations')
  const { conversationId: routeId } = useParams({ strict: false })
  const navigate = useNavigate()

  return useCallback(
    (id: ConversationId) => {
      // The route only loads an id that differs from the active one, and an unchanged URL raises
      // nothing to react to, so either case asks the store directly. Re-picking the active
      // conversation while another is still loading must also override that pending load.
      if (id === activeId || id === routeId) {
        conversations.open(id)
      }

      if (id !== routeId) {
        void navigate({ to: '/ui/c/$conversationId', params: { conversationId: id } })
      }
    },
    [activeId, conversations, navigate, routeId],
  )
}
