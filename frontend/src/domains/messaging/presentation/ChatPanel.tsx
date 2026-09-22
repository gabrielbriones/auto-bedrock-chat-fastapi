import { useCallback, useState, type ReactNode } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import type { MessageId } from '@/shared/kernel/branded'

import { MessageComposer } from '@/domains/messaging/presentation/MessageComposer'
import { Transcript } from '@/domains/messaging/presentation/Transcript'
import { composerAvailability } from '@/domains/messaging/presentation/composer-policy'

export type ChatPanelProps = {
  readonly inputEnabled: boolean
  /**
   * SPEC-013 §5 (Task 23 Phase 2): the preset bar + variable panel, supplied by the composition
   * root so messaging never imports prompt-catalog — contexts meet only in `src/app` (see
    * `ConversationView`). Rendered only in the empty transcript's welcome area.
   */
  readonly presetArea?: ReactNode
  /** FR-PROMPT-007: fired for every manually-sent message, before it goes out. */
  readonly onMessageSent?: (text: string) => void
  readonly renderFeedback?: (messageId: MessageId) => ReactNode
}

// SPEC-012 §5: badge, preset area (Task 23), transcript, composer.
export function ChatPanel({ inputEnabled, presetArea, onMessageSent, renderFeedback }: ChatPanelProps) {
  const { bootstrap, chatSession, notifications } = useContainer()
  const { connection, transcript, awaitingResponse } = useContainerStore('chatSession')
  const [draft, setDraft] = useState('')

  const availability = composerAvailability(
    {
      inputEnabled,
      connected: connection.kind === 'connected',
      awaitingResponse,
      lockWhileResponding: bootstrap.lockInputWhileResponding,
    },
    MESSAGING_COPY.composer.disabled,
  )

  const send = useCallback(
    (text: string) => {
      setDraft('')
      onMessageSent?.(text)

      // ADR-004: a dropped send is not queued, so the user is told rather than left guessing.
      if (chatSession.send(text) === 'dropped-closed') {
        notifications.error(MESSAGING_COPY.errors.dropped)
      }
    },
    [chatSession, notifications, onMessageSent],
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Transcript
        entries={transcript}
        welcome={bootstrap.uiWelcomeMessage}
        presetArea={presetArea}
        {...(renderFeedback === undefined ? {} : { renderFeedback })}
      />
      <MessageComposer
        value={draft}
        availability={availability}
        awaitingResponse={awaitingResponse}
        onChange={setDraft}
        onSend={send}
      />
    </div>
  )
}
