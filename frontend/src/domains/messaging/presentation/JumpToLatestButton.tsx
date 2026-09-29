import { Button } from '@/components/ui/button'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

const { transcript: COPY } = MESSAGING_COPY

export type JumpToLatestButtonProps = {
  readonly unread: number
  readonly onJump: () => void
}

// Sticky rather than fixed: the shell's <main> is the scroll container (FR-DS-008) and this must
// not introduce a second one. The offset clears the composer, which is stuck to the same edge.
export function JumpToLatestButton({ unread, onJump }: JumpToLatestButtonProps) {
  return (
    <div className="pointer-events-none sticky bottom-24 z-10 flex justify-center">
      <Button
        type="button"
        variant="secondary"
        onClick={onJump}
        aria-label={unread > 0 ? COPY.jumpToLatestUnread(unread) : COPY.jumpToLatest}
        className="pointer-events-auto shadow-md"
      >
        {COPY.jumpToLatest}
        {unread > 0 ? (
          <span aria-hidden="true" className="rounded-full bg-primary px-1.5 text-primary-foreground">
            {COPY.unreadCount(unread)}
          </span>
        ) : null}
      </Button>
    </div>
  )
}
