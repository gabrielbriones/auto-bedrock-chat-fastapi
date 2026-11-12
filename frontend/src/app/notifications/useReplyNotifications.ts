import { useEffect } from 'react'
import { useNavigate, useParams, useRouterState } from '@tanstack/react-router'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { useDocumentTitle } from '@/app/layouts/use-document-title'
import { REPLY_NOTIFICATION_COPY } from '@/shared/copy/reply-notifications'

// FR-MSG-032. The chat tab title carries the count of replies that landed while the user was away.
export const useReplyNotificationTitle = (appTitle: string): void => {
  const { unread } = useContainerStore('replyNotifications')

  useDocumentTitle(unread > 0 ? REPLY_NOTIFICATION_COPY.unreadTitle(unread, appTitle) : appTitle)
}

// FR-MSG-032. The adapter has already focused the window; this shows the conversation that got
// the reply. Route sync (FR-CONV-011) loads it, so an already-open one is not reloaded. Mounted at
// the router root, so a click works from any page (the dashboard included), not just the chat.
export const useReplyNotificationNavigation = (): void => {
  const { replyNotifications } = useContainer()
  const { conversationId: routeId } = useParams({ strict: false })
  const onChat = useRouterState({ select: (state) => /^\/ui(\/|$)/.test(state.location.pathname) })
  const navigate = useNavigate()

  useEffect(
    () =>
      replyNotifications.onActivate((id) => {
        if (id === null) {
          // A reply to a not-yet-saved chat: from elsewhere, bring the user back to it.
          if (!onChat) void navigate({ to: '/ui' })
        } else if (id !== routeId) {
          void navigate({ to: '/ui/c/$conversationId', params: { conversationId: id } })
        }
      }),
    [navigate, onChat, replyNotifications, routeId],
  )
}
