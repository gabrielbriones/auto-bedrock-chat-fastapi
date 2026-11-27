import { useCallback, useMemo, useState, type ReactNode } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import type { MessageId } from '@/shared/kernel/branded'

import { sessionUsage } from '@/domains/messaging/domain/session-usage'
import { MessageComposer } from '@/domains/messaging/presentation/MessageComposer'
import { SessionUsageSummary } from '@/domains/messaging/presentation/SessionUsageSummary'
import { Transcript } from '@/domains/messaging/presentation/Transcript'
import { composerAvailability } from '@/domains/messaging/presentation/composer-policy'
import type { ComposerCommands } from '@/domains/messaging/presentation/useSlashCommandMenu'

export type ChatPanelProps = {
  readonly inputEnabled: boolean
  /**
   * SPEC-013 §5 (Task 23 Phase 2): the preset bar + variable panel, supplied by the composition
   * root so messaging never imports prompt-catalog — contexts meet only in `src/app` (see
    * `ConversationView`). Rendered only in the empty transcript's welcome area.
   */
  readonly presetArea?: ReactNode
  /** Rendered inside the composer, beside the send button (the model picker, from `src/app`). */
  readonly composerToolbar?: ReactNode
  /** FR-PROMPT-007: fired for every manually-sent message, before it goes out. */
  readonly onMessageSent?: (text: string) => void
  readonly renderFeedback?: (messageId: MessageId) => ReactNode
  /** Skills offered when the draft starts with `/`, supplied from `src/app`. */
  readonly commands?: ComposerCommands | undefined
  /** Replaces the draft each time a new object is passed (e.g. a deep link's command). */
  readonly draftRequest?: { readonly text: string } | null
}

type DraftRequest = { readonly text: string } | null

// Each new request replaces the draft (adjusted while rendering, not in an effect): a refused slash
// command comes back to be corrected, and a deep link's command is put in to be reviewed.
const useExternalDraft = (requests: readonly DraftRequest[]) => {
  const [draft, setDraft] = useState('')
  const [applied, setApplied] = useState(requests)

  if (requests.some((request, index) => request !== applied[index])) {
    const next = requests.find((request, index) => request !== null && request !== applied[index])
    setApplied(requests)
    if (next != null) {
      setDraft(next.text)
    }
  }

  return [draft, setDraft] as const
}

// SPEC-012 §5: badge, preset area (Task 23), transcript, composer.
export function ChatPanel({ inputEnabled, presetArea, composerToolbar, onMessageSent, renderFeedback, commands, draftRequest = null }: ChatPanelProps) {
  const { bootstrap, chatSession, notifications } = useContainer()
  const { connection, transcript, awaitingResponse, rejectedCommand } = useContainerStore('chatSession')
  const [draft, setDraft] = useExternalDraft([rejectedCommand, draftRequest])
  const usage = useMemo(() => sessionUsage(transcript), [transcript])

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
    [chatSession, notifications, onMessageSent, setDraft],
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
        commands={commands}
        toolbar={
          <>
            {composerToolbar}
            <SessionUsageSummary usage={usage} />
          </>
        }
      />
    </div>
  )
}
