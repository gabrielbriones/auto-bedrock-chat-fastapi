import type { PageAttentionPort } from '@/shared/ports/page-attention-port'
import type { PreferenceStoragePort } from '@/shared/ports/preference-storage-port'
import type { SystemNotificationPort } from '@/shared/ports/system-notification-port'

import { BrowserSystemNotificationPort } from '@/app/adapters/browser-system-notification-port'
import { DocumentAttentionPort } from '@/app/adapters/document-attention-port'
import { LocalPreferenceStorage } from '@/app/adapters/local-preference-storage'
import type { ChatBootstrap } from '@/app/bootstrap/chat-bootstrap'
import {
  ReplyNotificationStore,
  type ReplyNotificationStoreOptions,
} from '@/app/notifications/reply-notification.store'
import type { ConversationStore } from '@/domains/conversation/application/conversation.store'

export type ReplyNotificationDependencies = {
  readonly systemNotifications?: SystemNotificationPort
  readonly pageAttention?: PageAttentionPort
  readonly preferenceStorage?: PreferenceStoragePort
}

// FR-MSG-032. Messaging says a reply settled; the roster says which conversation it belongs to and
// what it is called. Neither context imports the other, so they meet here.
export const createReplyNotifications = (
  bootstrap: Pick<ChatBootstrap, 'appTitle'>,
  stores: {
    readonly chatSession: ReplyNotificationStoreOptions['chatSession']
    readonly conversations: Pick<ConversationStore, 'getSnapshot'>
  },
  deps: ReplyNotificationDependencies,
): ReplyNotificationStore =>
  new ReplyNotificationStore({
    chatSession: stores.chatSession,
    system: deps.systemNotifications ?? new BrowserSystemNotificationPort(),
    attention: deps.pageAttention ?? new DocumentAttentionPort(),
    storage: deps.preferenceStorage ?? new LocalPreferenceStorage(),
    describe: (reply) => {
      const { activeId, items } = stores.conversations.getSnapshot()
      const id = reply.conversationId ?? activeId
      const title = items.find((conversation) => conversation.id === id)?.title ?? bootstrap.appTitle

      return { conversationId: id, title }
    },
  })
