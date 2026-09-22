import { FEEDBACK_COPY } from '@/shared/copy/feedback'

export function FeedbackSubmittedNotice() {
  return (
    <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
      {FEEDBACK_COPY.submitted}
    </p>
  )
}