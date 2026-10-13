import type { ConversationId } from '@/shared/kernel/branded'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import { EmptyState } from '@/components/ui/composed/empty-state'
import { Button } from '@/components/ui/button'
import type { ConversationGroup } from '@/domains/conversation/presentation/group-conversations'
import { ConversationListItem } from '@/domains/conversation/presentation/ConversationListItem'

export type ConversationListProps = {
  readonly groups: readonly ConversationGroup[]
  readonly activeId: ConversationId | null
  readonly selection: ReadonlySet<ConversationId>
  readonly selectionMode: boolean
  readonly onOpen: (id: ConversationId) => void
  readonly onToggleSelect: (id: ConversationId) => void
  readonly onRename: (id: ConversationId) => void
  readonly onDelete: (id: ConversationId) => void
  readonly onStartNew: () => void
}

// FR-CONV-013: the roster arrives already ordered by the aggregate; grouping preserves that order
// within each date section.
export function ConversationList({
  groups,
  activeId,
  selection,
  selectionMode,
  onOpen,
  onToggleSelect,
  onRename,
  onDelete,
  onStartNew,
}: ConversationListProps) {
  if (groups.length === 0) {
    return (
      <EmptyState
        title={CONVERSATION_COPY.sidebar.empty}
        description={CONVERSATION_COPY.sidebar.emptyHint}
        action={<Button variant="link" size="sm" onClick={onStartNew}>{CONVERSATION_COPY.sidebar.emptyAction}</Button>}
      />
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {groups.map(({ label, conversations }) => (
        <section key={label} aria-label={label}>
          <h2 className="sticky top-0 z-10 bg-card px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">
            {label}
          </h2>
          <ul className="flex flex-col gap-1 py-1">
            {conversations.map((conversation) => (
              <ConversationListItem
                key={conversation.id}
                conversation={conversation}
                active={conversation.id === activeId}
                selected={selection.has(conversation.id)}
                selectionMode={selectionMode}
                onOpen={onOpen}
                onToggleSelect={onToggleSelect}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
