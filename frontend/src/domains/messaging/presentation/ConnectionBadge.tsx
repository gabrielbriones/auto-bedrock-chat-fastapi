import { WifiOffIcon } from 'lucide-react'

import { cn } from '@/lib/utils'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

import type { ConnectionView } from '@/domains/messaging/domain/connection'

const { connection: COPY } = MESSAGING_COPY

export type ConnectionBadgeProps = {
  readonly connection: ConnectionView
  readonly className?: string
}

export function ConnectionBadge({ connection, className }: ConnectionBadgeProps) {
  if (connection.kind === 'connected' || connection.kind === 'connecting') {
    return null
  }

  return (
    <div
      role="alert"
      aria-label={COPY.label}
      className={cn(
        'flex items-start gap-3 border-b border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive',
        className,
      )}
    >
      <WifiOffIcon aria-hidden className="size-5 shrink-0" />
      <span className="min-w-0 break-words">{COPY.disconnected}</span>
    </div>
  )
}
