import { PlusIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import type { SelectionState } from '@/domains/conversation/domain/roster'

export type ConversationSidebarHeaderProps = {
  readonly selectionState: SelectionState
  readonly onToggleSelectAll: () => void
  readonly onStartNew: () => void
}

// FR-CONV-006: the select-all control carries a real indeterminate state, so a partial selection is
// not rendered as an unselected one.
export function ConversationSidebarHeader({
  selectionState,
  onToggleSelectAll,
  onStartNew,
}: ConversationSidebarHeaderProps) {
  return (
    <div className="flex items-center gap-2 border-b border-border p-2">
      <Checkbox
        checked={selectionState === 'all'}
        indeterminate={selectionState === 'partial'}
        onCheckedChange={onToggleSelectAll}
        aria-label={CONVERSATION_COPY.bulk.selectAll}
      />

      <Button variant="outline" size="sm" onClick={onStartNew} className="flex-1">
        <PlusIcon data-icon="inline-start" />
        {CONVERSATION_COPY.sidebar.newChat}
      </Button>
    </div>
  )
}
