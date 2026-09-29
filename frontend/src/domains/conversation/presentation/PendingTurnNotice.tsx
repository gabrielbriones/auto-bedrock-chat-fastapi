import { Button } from '@/components/ui/button'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import type { PendingTurnWatch } from '@/domains/conversation/domain/pending-turn'

export type PendingTurnNoticeProps = {
  readonly watch: PendingTurnWatch | null
  readonly onRetry: () => void
}

// FR-CONV-009 / FR-CONV-009c: polling is visible while it runs, and exhaustion offers a retry
// instead of the notice silently disappearing after two minutes.
export function PendingTurnNotice({ watch, onRetry }: PendingTurnNoticeProps) {
  if (watch === null) {
    return null
  }

  return (
    <div
      role="status"
      className="flex items-center gap-2 border-b border-border px-4 py-2 text-sm text-muted-foreground"
    >
      <span className="flex-1">
        {watch.exhausted ? CONVERSATION_COPY.pending.exhausted : CONVERSATION_COPY.pending.notice}
      </span>

      {watch.exhausted ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          {CONVERSATION_COPY.pending.retry}
        </Button>
      ) : null}
    </div>
  )
}
