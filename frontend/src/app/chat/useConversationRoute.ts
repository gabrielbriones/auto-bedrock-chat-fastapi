import { useEffect, useRef } from 'react'
import { useNavigate } from '@tanstack/react-router'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import type { ConversationId } from '@/shared/kernel/branded'
import {
  synchronizeConversationRoute,
  type ConversationRouteSyncState,
} from '@/app/chat/conversation-route-sync'

// FR-CONV-011. A URL can ask the store to load a conversation and a store selection can update the
// URL. The two directions are arbitrated here because independent effects can each react to the
// other's stale value. Routing stays in `src/app`, outside the conversation domain.
export const useConversationRoute = (routeId: ConversationId | null): void => {
  const { conversations } = useContainer()
  const { activeId, visible, connected, awaitingId } = useContainerStore('conversations')
  const navigate = useNavigate()
  const syncState = useRef<ConversationRouteSyncState>({
    previousActiveId: activeId,
    requestedRouteId: null,
  })

  // Route and store changes are arbitrated in one effect so neither direction can react to the
  // other's stale value. A changed active id came from a conversation response and wins over the
  // old URL; an otherwise changed URL is a direct navigation and asks the store to load it.
  useEffect(() => {
    const next = synchronizeConversationRoute({
      ...syncState.current,
      routeId,
      activeId,
      available: visible && connected,
      awaitingId,
    })
    syncState.current = next

    if (next.command.kind === 'load') {
      conversations.open(next.command.id)
    } else if (next.command.kind === 'navigate') {
      void (next.command.id === null
        ? navigate({ to: '/ui', replace: true })
        : navigate({
          to: '/ui/c/$conversationId',
            params: { conversationId: next.command.id },
            replace: true,
          }))
    }
  }, [activeId, awaitingId, connected, conversations, navigate, routeId, visible])
}