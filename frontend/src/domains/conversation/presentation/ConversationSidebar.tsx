import type { ReactNode } from 'react'

import { useContainerStore } from '@/app/bootstrap/container-context'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'

import { BulkDeleteBar } from '@/domains/conversation/presentation/BulkDeleteBar'
import { ConversationList } from '@/domains/conversation/presentation/ConversationList'
import { ConversationSidebarHeader } from '@/domains/conversation/presentation/ConversationSidebarHeader'
import { useConversationActions } from '@/domains/conversation/presentation/useConversationActions'

export type ConversationSidebarProps = {
  /** Closes the below-breakpoint drawer after an action that changes what is shown (FR-CONV-003). */
  readonly onNavigate?: () => void
  /** FR-IAM-016: supplied by the composition root (FR-TOOL-011 bars importing the iam context here). */
  readonly footer?: ReactNode
}

// SPEC-011 §5. Renders nothing at all when the roster is not available (FR-CONV-001) rather than an
// empty shell, so the chat view reclaims the space.
export function ConversationSidebar({ onNavigate, footer }: ConversationSidebarProps) {
  const snapshot = useContainerStore('conversations')
  const actions = useConversationActions(onNavigate === undefined ? {} : { onNavigate })

  if (!snapshot.visible) {
    return null
  }

  return (
    <nav aria-label={CONVERSATION_COPY.sidebar.label} className="flex h-full min-h-0 flex-col">
      <ConversationSidebarHeader
        selectionState={snapshot.selectionState}
        onToggleSelectAll={actions.toggleSelectAll}
        onStartNew={actions.startNew}
      />

      {/* FR-CONV-019 `conversation_history_unavailable`: in place, so it survives a toast timeout. */}
      {snapshot.notice !== null ? (
        <p role="status" className="border-b border-border p-2 text-sm text-muted-foreground">
          {snapshot.notice}
        </p>
      ) : null}

      <ConversationList
        items={snapshot.items}
        activeId={snapshot.activeId}
        selection={snapshot.selection}
        onOpen={actions.open}
        onToggleSelect={actions.toggleSelected}
        onRename={actions.rename}
        onDelete={actions.remove}
      />

      <BulkDeleteBar
        count={snapshot.selection.size}
        inFlight={snapshot.bulkDeleteInFlight}
        onClear={actions.clearSelected}
        onDelete={actions.removeSelected}
      />

      {footer !== undefined ? <div className="mt-auto">{footer}</div> : null}
    </nav>
  )
}
