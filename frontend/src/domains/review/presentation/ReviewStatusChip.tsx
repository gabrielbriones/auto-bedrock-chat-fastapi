import { Badge } from '@/components/ui/badge'
import type { ReviewStatus } from '@/domains/review/domain/public'

const LABELS: Readonly<Record<ReviewStatus, string>> = {
  pending_review: 'Pending review',
  approved: 'Approved',
  rejected: 'Rejected',
}

export function ReviewStatusChip({ status }: { readonly status: ReviewStatus }) {
  const variant = status === 'rejected' ? 'destructive' : status === 'approved' ? 'secondary' : 'outline'
  return <Badge variant={variant}>{LABELS[status]}</Badge>
}