import { Button } from '@/components/ui/button'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'

export type BulkDeleteBarProps = {
  readonly count: number
  readonly inFlight: boolean
  readonly onClear: () => void
  readonly onDelete: () => void
}

// FR-CONV-006: hidden at zero selection. `inFlight` disables the action so the single-flight guard
// is visible to the user rather than only enforced silently by the aggregate.
export function BulkDeleteBar({ count, inFlight, onClear, onDelete }: BulkDeleteBarProps) {
  if (count === 0) {
    return null
  }

  return (
    <div className="flex items-center gap-2 border-t border-border p-2">
      <p aria-live="polite" className="flex-1 truncate text-sm text-muted-foreground">
        {CONVERSATION_COPY.bulk.selected(count)}
      </p>

      <Button variant="ghost" size="sm" onClick={onClear}>
        {CONVERSATION_COPY.bulk.clear}
      </Button>

      <Button variant="destructive" size="sm" disabled={inFlight} onClick={onDelete}>
        {CONVERSATION_COPY.bulk.action}
      </Button>
    </div>
  )
}
