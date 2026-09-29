import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/composed/empty-state'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'

export type UnknownConversationProps = {
  readonly onStartNew: () => void
}

// FR-MSG-008 / T-096: a URL naming a conversation the server does not have is a recovery state with
// a way out, not an error boundary and not a blank transcript.
export function UnknownConversation({ onStartNew }: UnknownConversationProps) {
  return (
    <EmptyState
      title={CONVERSATION_COPY.unknown.title}
      description={CONVERSATION_COPY.unknown.description}
      action={<Button onClick={onStartNew}>{CONVERSATION_COPY.unknown.action}</Button>}
    />
  )
}
