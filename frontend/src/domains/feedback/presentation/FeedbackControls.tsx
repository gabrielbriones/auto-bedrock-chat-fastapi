import type { ReactNode } from 'react'
import { ThumbsDownIcon, ThumbsUpIcon } from 'lucide-react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { FEEDBACK_COPY } from '@/shared/copy/feedback'
import type { MessageId } from '@/shared/kernel/branded'

import {
  createFeedbackSubmission,
  type FeedbackDraft,
  type FeedbackSubmission,
} from '@/domains/feedback/domain/public'
import { CorrectionForm } from '@/domains/feedback/presentation/CorrectionForm'
import { FeedbackInlineError } from '@/domains/feedback/presentation/FeedbackInlineError'
import { FeedbackSubmittedNotice } from '@/domains/feedback/presentation/FeedbackSubmittedNotice'

export type FeedbackControlsProps = {
  readonly messageId: MessageId
}

const safeIdPart = (value: string): string => value.replace(/[^a-zA-Z0-9_-]/g, '-')

type RatingButtonsProps = {
  readonly submission: FeedbackSubmission
  readonly describedBy: Readonly<Record<string, string>>
  readonly disabled: boolean
  readonly onPositive: () => void
  readonly correctionPopover: ReactNode
}

function RatingButtons({ submission, describedBy, disabled, onPositive, correctionPopover }: RatingButtonsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        type="button"
        variant="outline"
        size="icon"
        aria-label={FEEDBACK_COPY.positive}
        aria-pressed={submission.rating === 'positive'}
        disabled={disabled}
        {...describedBy}
        onClick={onPositive}
        title={FEEDBACK_COPY.positive}
      >
        <ThumbsUpIcon aria-hidden />
      </Button>
      {correctionPopover}
    </div>
  )
}

type CorrectionPopoverProps = {
  readonly open: boolean
  readonly pressed: boolean
  readonly formId: string
  readonly describedBy: Readonly<Record<string, string>>
  readonly onOpenChange: (open: boolean) => void
  readonly onSubmit: (draft: FeedbackDraft) => void
}

function CorrectionPopover({ open, pressed, formId, describedBy, onOpenChange, onSubmit }: CorrectionPopoverProps) {
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger
        render={
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={FEEDBACK_COPY.negative}
            aria-pressed={pressed}
            title={FEEDBACK_COPY.negative}
            {...describedBy}
          />
        }
      >
        <ThumbsDownIcon aria-hidden />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80">
        <CorrectionForm id={formId} onCancel={() => onOpenChange(false)} onSubmit={onSubmit} />
      </PopoverContent>
    </Popover>
  )
}

export function FeedbackControls({ messageId }: FeedbackControlsProps) {
  const { feedback } = useContainer()
  const snapshot = useContainerStore('feedback')
  const submission = snapshot.submissions.get(messageId) ?? createFeedbackSubmission(messageId)
  const formId = `feedback-form-${safeIdPart(messageId)}`
  const errorId = `feedback-error-${safeIdPart(messageId)}`
  const isDrafting = submission.status === 'drafting'
  const isSubmitted = submission.status === 'submitting' || submission.status === 'submitted'
  const describedBy = submission.error === null ? {} : { 'aria-describedby': errorId }

  if (isSubmitted) {
    return <FeedbackSubmittedNotice />
  }

  const submitNegative = (draft: FeedbackDraft) => {
    feedback.submitNegative(messageId, draft)
  }

  const toggleCorrection = (open: boolean) => {
    if (open) {
      feedback.openCorrectionForm(messageId)
      return
    }
    feedback.cancelCorrectionForm(messageId)
  }

  return (
    <section aria-label={FEEDBACK_COPY.prompt} className="mt-3 grid gap-2">
      <p>{FEEDBACK_COPY.prompt}</p>
      <RatingButtons
        submission={submission}
        describedBy={describedBy}
        disabled={isDrafting}
        onPositive={() => feedback.ratePositive(messageId)}
        correctionPopover={
          <CorrectionPopover
            open={isDrafting}
            pressed={submission.rating === 'negative'}
            formId={formId}
            describedBy={describedBy}
            onOpenChange={toggleCorrection}
            onSubmit={submitNegative}
          />
        }
      />
      {submission.error === null ? null : <FeedbackInlineError id={errorId} message={submission.error.message} />}
    </section>
  )
}