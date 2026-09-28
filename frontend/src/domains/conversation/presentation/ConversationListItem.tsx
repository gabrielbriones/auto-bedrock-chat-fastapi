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

function setTitleSlideDuration(row: HTMLElement): void {
  const viewport = row.querySelector<HTMLElement>('.conversation-title-viewport')
  const text = row.querySelector<HTMLElement>('.conversation-title-text')
  if (!viewport || !text) return

  const availableWidth = viewport.clientWidth - parseFloat(getComputedStyle(viewport).paddingRight)
  const distance = Math.max(0, text.scrollWidth - availableWidth)
  text.style.setProperty('--title-slide-duration', `${Math.max(distance / 40, 0.1)}s`)
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
// the checkbox or the options trigger cannot reach it because it never contains them. On pointer
// devices the stylesheet floats the trigger over the title's right end, so the title keeps the
// full row width until the row is hovered.
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
    <li
      className="conversation-row group relative flex items-center gap-1 px-2"
      onMouseEnter={(event) => setTitleSlideDuration(event.currentTarget)}
      onFocus={(event) => setTitleSlideDuration(event.currentTarget)}
    >
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
        className={cn(
          // The row stays lit while the pointer is on the floating options trigger or its menu is
          // open; the trigger sits beside this button rather than inside it.
          'min-w-0 flex-1 justify-start text-left group-hover:bg-muted group-hover:text-foreground dark:group-hover:bg-muted/50',
          'group-has-aria-expanded:bg-muted group-has-aria-expanded:text-foreground',
          active && 'bg-muted text-foreground',
        )}
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
