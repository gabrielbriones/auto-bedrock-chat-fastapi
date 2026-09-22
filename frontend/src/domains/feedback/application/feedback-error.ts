import { FEEDBACK_COPY } from '@/shared/copy/feedback'

import type { FeedbackFailure } from '@/domains/feedback/domain/feedback-submission'

const CODE_MESSAGES: Readonly<Record<string, string>> = {
  feedback_unavailable: FEEDBACK_COPY.errors.unavailable,
  unauthorized_feedback: FEEDBACK_COPY.errors.unauthorized,
  invalid_feedback: FEEDBACK_COPY.errors.invalid,
  no_active_conversation: FEEDBACK_COPY.errors.inactive,
}

export const mapFeedbackError = (failure: FeedbackFailure): FeedbackFailure => ({
  code: failure.code,
  message: CODE_MESSAGES[failure.code] ?? failure.message,
})