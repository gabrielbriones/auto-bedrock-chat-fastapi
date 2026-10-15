import { useCallback } from 'react'
import { useNavigate } from '@tanstack/react-router'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import type { ConversationId } from '@/shared/kernel/branded'

// FR-CONV-003 / FR-CONV-011. A sidebar pick moves the URL straight away and the route asks the
// store to load it, so the address bar never waits on the server's reply (XMGPLAT-11999).
export const useOpenConversation = (): ((id: ConversationId) => void) => {
  const { conversations } = useContainer()
  const { activeId } = useContainerStore('conversations')
  const navigate = useNavigate()

  return useCallback(
    (id: ConversationId) => {
      // Same URL means no route change to react to, so the reload is asked for directly.
      if (id === activeId) {
        conversations.open(id)
        return
      }

      void navigate({ to: '/ui/c/$conversationId', params: { conversationId: id } })
    },
    [activeId, conversations, navigate],
  )
}
