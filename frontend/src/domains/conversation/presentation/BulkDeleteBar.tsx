import { Trash2Icon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import type { SelectionState } from '@/domains/conversation/domain/roster'

export type BulkDeleteBarProps = {
  readonly count: number
  readonly inFlight: boolean
  readonly selectionState: SelectionState
  readonly onToggleSelectAll: () => void
  readonly onClear: () => void
  readonly onDelete: () => void
}

// FR-CONV-006: the select-all control carries a real indeterminate state, so a partial selection is
// not rendered as an unselected one. `inFlight` disables the action so the single-flight guard
// is visible to the user rather than only enforced silently by the aggregate.
export function BulkDeleteBar({
  count,
  inFlight,
  selectionState,
  onToggleSelectAll,
  onClear,
  onDelete,
}: BulkDeleteBarProps) {
  return (
    <div className="grid gap-1.5 border-t border-border px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Checkbox
          checked={selectionState === 'all'}
          indeterminate={selectionState === 'partial'}
          onCheckedChange={onToggleSelectAll}
          aria-label={CONVERSATION_COPY.bulk.selectAll}
        />
        <p aria-live="polite" className="me-auto whitespace-nowrap text-sm font-medium tabular-nums">
          {CONVERSATION_COPY.bulk.selected(count)}
        </p>
        {count > 0 ? (
          <Button variant="ghost" size="xs" className="text-muted-foreground" onClick={onClear}>
            {CONVERSATION_COPY.bulk.clear}
          </Button>
        ) : null}
      </div>
      <Button
        variant="destructive"
        size="sm"
        className="w-full"
        disabled={count === 0 || inFlight}
        onClick={onDelete}
      >
        <Trash2Icon aria-hidden />
        {CONVERSATION_COPY.bulk.action}
      </Button>
    </div>
  )
}
