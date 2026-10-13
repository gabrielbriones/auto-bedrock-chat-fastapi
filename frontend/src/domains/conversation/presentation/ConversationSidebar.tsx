import { useState } from 'react'
import type { ReactNode } from 'react'

import { useContainer, useContainerStore, type ContainerSnapshot } from '@/app/bootstrap/container-context'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'

import { BulkDeleteBar } from '@/domains/conversation/presentation/BulkDeleteBar'
import { ConversationList } from '@/domains/conversation/presentation/ConversationList'
import { ConversationSidebarHeader } from '@/domains/conversation/presentation/ConversationSidebarHeader'
import { groupConversations } from '@/domains/conversation/presentation/group-conversations'
import { useConversationActions, type ConversationActions } from '@/domains/conversation/presentation/useConversationActions'

export type ConversationSidebarProps = {
  /** Desktop rail state; the drawer never collapses, so it leaves these unset. */
  readonly collapsed?: boolean
  readonly onToggleCollapsed?: () => void
  /** Closes the below-breakpoint drawer after an action that changes what is shown (FR-CONV-003). */
  readonly onNavigate?: () => void
  /** FR-IAM-016: supplied by the composition root (FR-TOOL-011 bars importing the iam context here). */
  readonly footer?: ReactNode
}

// SPEC-011 §5. Renders nothing at all when the roster is not available (FR-CONV-001) rather than an
// empty shell, so the chat view reclaims the space.
export function ConversationSidebar({ collapsed = false, onToggleCollapsed, onNavigate, footer }: ConversationSidebarProps) {
  const snapshot = useContainerStore('conversations')
  const actions = useConversationActions(onNavigate === undefined ? {} : { onNavigate })
  const [selectionMode, setSelectionMode] = useState(false)

  if (!snapshot.visible) {
    return null
  }

  const toggleSelectionMode = () => {
    if (selectionMode) actions.clearSelected()
    setSelectionMode(!selectionMode)
  }

  return (
    <nav aria-label={CONVERSATION_COPY.sidebar.label} className="flex h-full min-h-0 flex-col">
      <ConversationSidebarHeader
        hasItems={snapshot.items.length > 0}
        selectionMode={selectionMode}
        onToggleSelectionMode={toggleSelectionMode}
        collapsed={collapsed}
        onToggleCollapsed={onToggleCollapsed}
        onStartNew={actions.startNew}
      />

      {collapsed ? null : (
        <SidebarBody snapshot={snapshot} actions={actions} selectionMode={selectionMode} />
      )}

      {footer !== undefined ? <div className="mt-auto">{footer}</div> : null}
    </nav>
  )
}

function SidebarBody({ snapshot, actions, selectionMode }: {
  readonly snapshot: ContainerSnapshot<'conversations'>
  readonly actions: ConversationActions
  readonly selectionMode: boolean
}) {
  const { clock } = useContainer()
  const groups = groupConversations(snapshot.items, new Date(clock.now().epochMilliseconds))

  return (
    <>
      {/* FR-CONV-019 `conversation_history_unavailable`: in place, so it survives a toast timeout. */}
      {snapshot.notice !== null ? (
        <p role="status" className="border-b border-border p-2 text-sm text-muted-foreground">
          {snapshot.notice}
        </p>
      ) : null}

      <ConversationList
        groups={groups}
        activeId={snapshot.activeId}
        selection={snapshot.selection}
        selectionMode={selectionMode}
        onOpen={actions.open}
        onToggleSelect={actions.toggleSelected}
        onRename={actions.rename}
        onDelete={actions.remove}
        onStartNew={actions.startNew}
      />

      {selectionMode ? (
        <BulkDeleteBar
          count={snapshot.selection.size}
          inFlight={snapshot.bulkDeleteInFlight}
          selectionState={snapshot.selectionState}
          onToggleSelectAll={actions.toggleSelectAll}
          onClear={actions.clearSelected}
          onDelete={actions.removeSelected}
        />
      ) : null}
    </>
  )
}
