import type { MessageId } from '@/shared/kernel/branded'
import { err, ok, type Result } from '@/shared/kernel/result'

export type FeedbackRating = 'positive' | 'negative'
export type FeedbackStatus = 'idle' | 'drafting' | 'submitting' | 'submitted' | 'failed'

export type FeedbackDraft = {
  readonly correctionText?: string
  readonly userComment?: string
}

export type FeedbackFailure = {
  readonly code: string
  readonly message: string
}

export type FeedbackSubmission = {
  readonly messageId: MessageId
  readonly rating: FeedbackRating | null
  readonly correctionText?: string
  readonly userComment?: string
  readonly status: FeedbackStatus
  readonly error: FeedbackFailure | null
}

export type FeedbackPayload = {
  readonly message_id: string
  readonly rating: FeedbackRating
  readonly correction_text?: string
  readonly user_comment?: string
}

export type IllegalFeedbackTransition = {
  readonly kind: 'illegal-transition'
  readonly from: FeedbackStatus
  readonly to: FeedbackStatus
}

export type FeedbackResult = Result<FeedbackSubmission, IllegalFeedbackTransition>

export const createFeedbackSubmission = (messageId: MessageId): FeedbackSubmission => ({
  messageId,
  rating: null,
  status: 'idle',
  error: null,
})

export const submittedFeedbackSubmission = (messageId: MessageId): FeedbackSubmission => ({
  messageId,
  rating: null,
  status: 'submitted',
  error: null,
})

const transition = (
  to: FeedbackStatus,
  next: Omit<FeedbackSubmission, 'status'>,
): FeedbackResult =>
  ok({ ...next, status: to })

const rejectedTransition = (submission: FeedbackSubmission, to: FeedbackStatus): FeedbackResult =>
  err({ kind: 'illegal-transition', from: submission.status, to })

const canOpenCorrectionForm = (status: FeedbackStatus): boolean =>
  status === 'idle' || status === 'failed'

const canStartPositive = (status: FeedbackStatus): boolean => status === 'idle' || status === 'failed'

const normalizedDraft = (draft: FeedbackDraft): FeedbackDraft => {
  const correctionText = draft.correctionText?.trim()
  const userComment = draft.userComment?.trim()

  return {
    ...(correctionText === undefined || correctionText === '' ? {} : { correctionText }),
    ...(userComment === undefined || userComment === '' ? {} : { userComment }),
  }
}

export const openCorrectionForm = (submission: FeedbackSubmission): FeedbackResult => {
  if (!canOpenCorrectionForm(submission.status)) {
    return rejectedTransition(submission, 'drafting')
  }

  return transition('drafting', {
    messageId: submission.messageId,
    rating: 'negative',
    error: null,
  })
}

export const cancelCorrectionForm = (submission: FeedbackSubmission): FeedbackResult => {
  if (submission.status !== 'drafting') {
    return rejectedTransition(submission, 'idle')
  }

  return transition('idle', {
    messageId: submission.messageId,
    rating: null,
    error: null,
  })
}

export const startPositiveSubmission = (submission: FeedbackSubmission): FeedbackResult => {
  if (!canStartPositive(submission.status)) {
    return rejectedTransition(submission, 'submitting')
  }

  return transition('submitting', {
    messageId: submission.messageId,
    rating: 'positive',
    error: null,
  })
}

export const startNegativeSubmission = (
  submission: FeedbackSubmission,
  draft: FeedbackDraft,
): FeedbackResult => {
  if (submission.status !== 'drafting') {
    return rejectedTransition(submission, 'submitting')
  }

  const normalized = normalizedDraft(draft)

  return transition('submitting', {
    messageId: submission.messageId,
    rating: 'negative',
    ...normalized,
    error: null,
  })
}

export const markFeedbackSubmitted = (submission: FeedbackSubmission): FeedbackResult => {
  if (submission.status !== 'submitting') {
    return rejectedTransition(submission, 'submitted')
  }

  return transition('submitted', {
    ...submission,
    error: null,
  })
}

export const revertFeedback = (
  submission: FeedbackSubmission,
  failure: FeedbackFailure,
): FeedbackResult => {
  if (submission.status !== 'submitting' && submission.status !== 'submitted') {
    return rejectedTransition(submission, 'failed')
  }

  return transition('failed', {
    ...submission,
    error: failure,
  })
}

export const toFeedbackPayload = (submission: FeedbackSubmission): FeedbackPayload | null => {
  if (submission.rating === null || submission.status !== 'submitting') {
    return null
  }

  return {
    message_id: submission.messageId,
    rating: submission.rating,
    ...(submission.correctionText === undefined ? {} : { correction_text: submission.correctionText }),
    ...(submission.userComment === undefined ? {} : { user_comment: submission.userComment }),
  }
}