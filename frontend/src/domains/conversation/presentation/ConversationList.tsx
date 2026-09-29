import type { ConversationId } from '@/shared/kernel/branded'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import { EmptyState } from '@/components/ui/composed/empty-state'
import type { Conversation } from '@/domains/conversation/domain/conversation'
import { ConversationListItem } from '@/domains/conversation/presentation/ConversationListItem'

export type ConversationListProps = {
  readonly items: readonly Conversation[]
  readonly activeId: ConversationId | null
  readonly selection: ReadonlySet<ConversationId>
  readonly onOpen: (id: ConversationId) => void
  readonly onToggleSelect: (id: ConversationId) => void
  readonly onRename: (id: ConversationId) => void
  readonly onDelete: (id: ConversationId) => void
}

// FR-CONV-013: the roster arrives already ordered by the aggregate, so this only renders it.
export function ConversationList({
  items,
  activeId,
  selection,
  onOpen,
  onToggleSelect,
  onRename,
  onDelete,
}: ConversationListProps) {
  if (items.length === 0) {
    return (
      <EmptyState
        title={CONVERSATION_COPY.sidebar.empty}
        description={CONVERSATION_COPY.sidebar.emptyHint}
      />
    )
  }

  return (
    <ul className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto py-1">
      {items.map((conversation) => (
        <ConversationListItem
          key={conversation.id}
          conversation={conversation}
          active={conversation.id === activeId}
          selected={selection.has(conversation.id)}
          onOpen={onOpen}
          onToggleSelect={onToggleSelect}
          onRename={onRename}
          onDelete={onDelete}
        />
      ))}
    </ul>
  )
}
