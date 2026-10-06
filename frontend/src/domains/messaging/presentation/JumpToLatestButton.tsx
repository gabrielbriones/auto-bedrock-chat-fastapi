import { ArrowDownIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

const { transcript: COPY } = MESSAGING_COPY

export type JumpToLatestButtonProps = {
  readonly visible: boolean
  readonly unread: number
  readonly onJump: () => void
}

// Sticky rather than fixed: the shell's <main> is the scroll container (FR-DS-008) and this must
// not introduce a second one. The offset clears the composer, stuck to the same edge, using the
// height it publishes as --composer-height. Always mounted so it can animate out; zero height (with
// -mt-4 cancelling the parent gap) keeps it from reserving layout space while hidden.
export function JumpToLatestButton({ visible, unread, onJump }: JumpToLatestButtonProps) {
  return (
    <div
      aria-hidden={!visible}
      inert={!visible}
      className="pointer-events-none sticky bottom-[calc(var(--composer-height,8rem)+0.75rem)] z-20 -mt-4 flex h-0 items-end justify-center"
    >
      <Button
        type="button"
        size="icon"
        onClick={onJump}
        aria-label={unread > 0 ? COPY.jumpToLatestUnread(unread) : COPY.jumpToLatest}
        title={COPY.jumpToLatest}
        className={`relative size-9 rounded-full border border-border bg-foreground text-background shadow-lg transition-[opacity,translate,scale] duration-200 ease-out hover:bg-foreground/85 motion-reduce:transition-none ${
          visible ? 'pointer-events-auto translate-y-0 scale-100 opacity-100' : 'translate-y-2 scale-90 opacity-0'
        }`}
      >
        <ArrowDownIcon aria-hidden="true" className="size-4" />
        {unread > 0 ? (
          <span
            aria-hidden="true"
            className="absolute -end-1.5 -top-1.5 min-w-5 rounded-full bg-primary px-1 text-xs leading-5 text-primary-foreground"
          >
            {COPY.unreadCount(unread)}
          </span>
        ) : null}
      </Button>
    </div>
  )
}
