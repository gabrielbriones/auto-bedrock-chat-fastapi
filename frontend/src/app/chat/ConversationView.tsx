import { useCallback } from 'react'
import { useNavigate } from '@tanstack/react-router'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { useConversationRoute } from '@/app/chat/useConversationRoute'
import { PromptCatalogArea } from '@/app/chat/PromptCatalogArea'
import type { ConversationId, MessageId } from '@/shared/kernel/branded'
import { PendingTurnNotice } from '@/domains/conversation/presentation/PendingTurnNotice'
import { UnknownConversation } from '@/domains/conversation/presentation/UnknownConversation'
import { ChatPanel } from '@/domains/messaging/presentation/ChatPanel'
import { FeedbackControls } from '@/domains/feedback/presentation/FeedbackControls'

export type ConversationViewProps = {
  /** Null at `/chat/ui`, which is a new, unsaved conversation (FR-CONV-011). */
  readonly conversationId: ConversationId | null
}

// Both chat routes render this. The composition root is where `conv`, `messaging` and
// `prompt-catalog` meet: no context imports another, so the pending-turn notice, the transcript
// and the preset bar are all joined here instead (DESIGN-002 §3 / `PromptCatalogArea`).
export function ConversationView({ conversationId }: ConversationViewProps) {
  const { bootstrap, conversations, promptCatalog } = useContainer()
  const { inputEnabled } = useContainerStore('identity')
  const { pendingTurn, unknownId } = useContainerStore('conversations')
  const navigate = useNavigate()

  useConversationRoute(conversationId)

  const startNew = useCallback(() => {
    conversations.dismissUnknownId()
    void navigate({ to: '/', replace: true })
  }, [conversations, navigate])

  const retry = useCallback(() => {
    conversations.retryPendingTurn()
  }, [conversations])

  const onMessageSent = useCallback(
    (text: string) => {
      promptCatalog.detectFromMessage(text)
    },
    [promptCatalog],
  )

  // Identity-stable on purpose: `MessageBubble` is memoised on this prop, so a fresh closure per
  // render would re-parse every message's Markdown on every snapshot.
  const feedbackControls = useCallback(
    (messageId: MessageId) => <FeedbackControls messageId={messageId} />,
    [],
  )
  const renderFeedback = bootstrap.feedbackEnabled ? feedbackControls : undefined

  // FR-MSG-008: only the id this route actually asked for gets the recovery state.
  if (unknownId !== null && unknownId === conversationId) {
    return <UnknownConversation onStartNew={startNew} />
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PendingTurnNotice watch={pendingTurn} onRetry={retry} />
      <ChatPanel
        inputEnabled={inputEnabled}
        onMessageSent={onMessageSent}
        presetArea={<PromptCatalogArea />}
        {...(renderFeedback === undefined ? {} : { renderFeedback })}
      />
    </div>
  )
}
