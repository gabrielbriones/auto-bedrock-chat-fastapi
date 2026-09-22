import { memo } from 'react'
import { MoreHorizontalIcon, PencilIcon, Trash2Icon } from 'lucide-react'

import '@/domains/conversation/presentation/conversation-list-item.css'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import { cn } from '@/lib/utils'
import type { ConversationId } from '@/shared/kernel/branded'
import type { Conversation } from '@/domains/conversation/domain/conversation'

export type ConversationListItemProps = {
  readonly conversation: Conversation
  readonly active: boolean
  readonly selected: boolean
  readonly onOpen: (id: ConversationId) => void
  readonly onToggleSelect: (id: ConversationId) => void
  readonly onRename: (id: ConversationId) => void
  readonly onDelete: (id: ConversationId) => void
}

function ConversationOptions({ title, onRename, onDelete }: {
  readonly title: string
  readonly onRename: () => void
  readonly onDelete: () => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            className="conversation-options shrink-0"
            aria-label={CONVERSATION_COPY.item.options(title)}
            title={CONVERSATION_COPY.item.options(title)}
          />
        }
      >
        <MoreHorizontalIcon aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={onRename}>
          <PencilIcon aria-hidden />
          {CONVERSATION_COPY.item.rename}
        </DropdownMenuItem>
        <DropdownMenuItem variant="destructive" onClick={onDelete}>
          <Trash2Icon aria-hidden />
          {CONVERSATION_COPY.item.delete}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// FR-CONV-014 / FR-CONV-015. Three real controls side by side rather than one clickable row with
// nested handlers: `Enter` and `Space` activate the title because it is a button, and a click on
// the checkbox or the options trigger cannot reach it because it never contains them.
// Memoised with id-taking callbacks: every store emission and route change re-renders the sidebar,
// and a dropdown menu per row made that cost tens of milliseconds a commit.
export const ConversationListItem = memo(function ConversationListItem({
  conversation,
  active,
  selected,
  onOpen,
  onToggleSelect,
  onRename,
  onDelete,
}: ConversationListItemProps) {
  const title = conversation.title ?? CONVERSATION_COPY.untitled
  const { id } = conversation

  return (
    <li className="conversation-row flex items-center gap-1 px-2">
      <Checkbox
        checked={selected}
        onCheckedChange={() => onToggleSelect(id)}
        aria-label={CONVERSATION_COPY.item.select(title)}
      />

      <Button
        variant="ghost"
        size="sm"
        onClick={() => onOpen(id)}
        aria-current={active ? 'true' : undefined}
        title={title}
        className={cn('min-w-0 flex-1 justify-start', active && 'bg-muted text-foreground')}
      >
        <span className="conversation-title-viewport">
          <span className="conversation-title-text">{title}</span>
        </span>
      </Button>

      <ConversationOptions
        title={title}
        onRename={() => onRename(id)}
        onDelete={() => onDelete(id)}
      />
    </li>
  )
})
