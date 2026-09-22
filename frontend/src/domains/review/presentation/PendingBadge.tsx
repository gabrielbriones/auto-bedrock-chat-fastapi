import { useContainerStore } from '@/app/bootstrap/container-context'
import { Badge } from '@/components/ui/badge'
import { REVIEW_COPY } from '@/shared/copy/review'

const PENDING_CAP = 99

// FR-REV-020/021: a nav-rail probe that shows a count, is hidden at zero, and never blocks the
// nav on a failed fetch — `pendingCount` is `null` until the first successful probe resolves.
export function PendingBadge() {
  const { pendingCount } = useContainerStore('reviews')

  if (pendingCount === null || pendingCount === 0) {
    return null
  }

  const label = pendingCount > PENDING_CAP ? REVIEW_COPY.badge.pendingCapped : REVIEW_COPY.badge.pending(pendingCount)

  return (
    <Badge variant="secondary" aria-label={label} title={label}>
      {pendingCount > PENDING_CAP ? '99+' : pendingCount}
    </Badge>
  )
}
