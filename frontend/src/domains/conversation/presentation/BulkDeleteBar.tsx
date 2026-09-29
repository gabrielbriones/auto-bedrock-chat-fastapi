import { Trash2Icon } from 'lucide-react'

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
    <div className="grid gap-1.5 border-t border-border px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <p aria-live="polite" className="whitespace-nowrap text-sm font-medium tabular-nums">
          {CONVERSATION_COPY.bulk.selected(count)}
        </p>
        <Button variant="ghost" size="xs" className="text-muted-foreground" onClick={onClear}>
          {CONVERSATION_COPY.bulk.clear}
        </Button>
      </div>
      <Button variant="destructive" size="sm" className="w-full" disabled={inFlight} onClick={onDelete}>
        <Trash2Icon aria-hidden />
        {CONVERSATION_COPY.bulk.action}
      </Button>
    </div>
  )
}
