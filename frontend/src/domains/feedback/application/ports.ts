import type { MessageId } from '@/shared/kernel/branded'
import type { SendResult, Unsubscribe } from '@/shared/ws/socket-client'

import type { FeedbackFailure, FeedbackPayload } from '@/domains/feedback/domain/feedback-submission'

export type FeedbackAck = {
  readonly messageId: MessageId
  readonly feedbackId: string
  readonly status: string
}

export type FeedbackGatewayError = FeedbackFailure & {
  readonly messageId: MessageId | null
}

export interface FeedbackGateway {
  submit(payload: FeedbackPayload): SendResult
  onAck(callback: (ack: FeedbackAck) => void): Unsubscribe
  onError(callback: (error: FeedbackGatewayError) => void): Unsubscribe
}

export interface SubmittedFeedbackLog {
  all(): ReadonlySet<MessageId>
  mark(id: MessageId): void
  unmark(id: MessageId): void
}