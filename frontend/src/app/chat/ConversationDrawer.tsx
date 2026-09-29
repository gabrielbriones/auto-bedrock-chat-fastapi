import type { ReactNode } from 'react'

import { PanelLeftIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import { ConversationSidebar } from '@/domains/conversation/presentation/ConversationSidebar'

export type ConversationDrawerProps = {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly onNavigate: () => void
  readonly footer?: ReactNode
}

// T-025 / FR-SHELL-018: below `lg` the roster moves into a sheet. It renders the same
// `ConversationSidebar` as the persistent column, so there is one list, not two that drift.
export function ConversationDrawer({ open, onOpenChange, onNavigate, footer }: ConversationDrawerProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="left" className="p-0 lg:hidden">
        <SheetHeader>
          <SheetTitle>{CONVERSATION_COPY.sidebar.label}</SheetTitle>
        </SheetHeader>
        <ConversationSidebar onNavigate={onNavigate} footer={footer} />
      </SheetContent>
    </Sheet>
  )
}

export type ConversationDrawerTriggerProps = {
  readonly onOpen: () => void
}

export function ConversationDrawerTrigger({ onOpen }: ConversationDrawerTriggerProps) {
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className="lg:hidden"
      aria-label={CONVERSATION_COPY.sidebar.open}
      onClick={onOpen}
    >
      <PanelLeftIcon />
    </Button>
  )
}
