import { PanelLeftCloseIcon, PanelLeftIcon, PlusIcon, Trash2Icon, XIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'

export type ConversationSidebarHeaderProps = {
  readonly hasItems: boolean
  readonly selectionMode: boolean
  readonly onToggleSelectionMode: () => void
  readonly collapsed?: boolean
  readonly onToggleCollapsed?: (() => void) | undefined
  readonly onStartNew: () => void
}

function NewChatIconButton({ onStartNew, className, variant = 'ghost' }: {
  readonly onStartNew: () => void
  readonly className?: string
  readonly variant?: 'ghost' | 'outline'
}) {
  return (
    <Button
      variant={variant}
      size="icon-sm"
      className={className}
      onClick={onStartNew}
      aria-label={CONVERSATION_COPY.sidebar.newChat}
      title={CONVERSATION_COPY.sidebar.newChat}
    >
      <PlusIcon aria-hidden />
    </Button>
  )
}

// Rail: only what still makes sense at icon width — expand, and start a chat.
function CollapsedSidebarHeader({ onToggleCollapsed, onStartNew }: {
  readonly onToggleCollapsed?: (() => void) | undefined
  readonly onStartNew: () => void
}) {
  return (
    <div className="flex flex-col items-center gap-2 border-b border-border p-2">
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={onToggleCollapsed}
        aria-label={CONVERSATION_COPY.sidebar.expand}
        title={CONVERSATION_COPY.sidebar.expand}
      >
        <PanelLeftIcon aria-hidden />
      </Button>
      <NewChatIconButton onStartNew={onStartNew} variant="outline" />
    </div>
  )
}

export function ConversationSidebarHeader({
  hasItems,
  selectionMode,
  onToggleSelectionMode,
  collapsed = false,
  onToggleCollapsed,
  onStartNew,
}: ConversationSidebarHeaderProps) {
  if (collapsed) {
    return <CollapsedSidebarHeader onToggleCollapsed={onToggleCollapsed} onStartNew={onStartNew} />
  }

  return (
    <div className="flex items-center gap-2 border-b border-border px-3 py-1.5">
      {onToggleCollapsed !== undefined ? (
        <Button
          variant="ghost"
          size="icon-sm"
          className="-ms-1 hidden lg:inline-flex"
          onClick={onToggleCollapsed}
          aria-label={CONVERSATION_COPY.sidebar.collapse}
          title={CONVERSATION_COPY.sidebar.collapse}
        >
          <PanelLeftCloseIcon aria-hidden />
        </Button>
      ) : null}
      <span className="text-sm font-medium text-foreground">{CONVERSATION_COPY.sidebar.label}</span>
      <div className="ms-auto flex items-center gap-1">
        {hasItems ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-pressed={selectionMode}
            aria-label={selectionMode ? CONVERSATION_COPY.sidebar.done : CONVERSATION_COPY.sidebar.select}
            title={selectionMode ? CONVERSATION_COPY.sidebar.done : CONVERSATION_COPY.sidebar.select}
            onClick={onToggleSelectionMode}
          >
            {selectionMode ? <XIcon aria-hidden /> : <Trash2Icon aria-hidden />}
          </Button>
        ) : null}
        <NewChatIconButton onStartNew={onStartNew} className="-me-1" />
      </div>
    </div>
  )
}
