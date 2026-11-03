import { useEffect } from 'react'
import { useNavigate, useParams } from '@tanstack/react-router'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { useDocumentTitle } from '@/app/layouts/use-document-title'
import { REPLY_NOTIFICATION_COPY } from '@/shared/copy/reply-notifications'

// FR-MSG-032. The adapter has already focused the window; this shows the conversation that got
// the reply. Route sync (FR-CONV-011) loads it, so an already-open one is not reloaded.
export const useReplyNotifications = (appTitle: string): void => {
  const { replyNotifications } = useContainer()
  const { unread } = useContainerStore('replyNotifications')
  const { conversationId: routeId } = useParams({ strict: false })
  const navigate = useNavigate()

  useDocumentTitle(unread > 0 ? REPLY_NOTIFICATION_COPY.unreadTitle(unread, appTitle) : appTitle)

  useEffect(
    () =>
      replyNotifications.onActivate((id) => {
        if (id !== null && id !== routeId) {
          void navigate({ to: '/ui/c/$conversationId', params: { conversationId: id } })
        }
      }),
    [navigate, replyNotifications, routeId],
  )
}
