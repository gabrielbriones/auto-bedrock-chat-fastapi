import { FEEDBACK_COPY } from '@/shared/copy/feedback'
import type { MessageId } from '@/shared/kernel/branded'
import { isOk } from '@/shared/kernel/result'
import type { SendResult, Unsubscribe } from '@/shared/ws/socket-client'

import type {
  FeedbackGateway,
  FeedbackGatewayError,
  SubmittedFeedbackLog,
} from '@/domains/feedback/application/ports'
import { mapFeedbackError } from '@/domains/feedback/application/feedback-error'
import {
  cancelCorrectionForm,
  createFeedbackSubmission,
  markFeedbackSubmitted,
  openCorrectionForm,
  revertFeedback,
  startNegativeSubmission,
  startPositiveSubmission,
  submittedFeedbackSubmission,
  toFeedbackPayload,
  type FeedbackDraft,
  type FeedbackSubmission,
} from '@/domains/feedback/domain/public'

export type FeedbackSnapshot = {
  readonly submissions: ReadonlyMap<MessageId, FeedbackSubmission>
}

export type FeedbackActionResult = SendResult | 'ignored'

export type FeedbackStoreOptions = {
  readonly gateway: FeedbackGateway
  readonly submittedLog: SubmittedFeedbackLog
}

const hydratedSubmissions = (submittedLog: SubmittedFeedbackLog): Map<MessageId, FeedbackSubmission> =>
  new Map([...submittedLog.all()].map((id) => [id, submittedFeedbackSubmission(id)]))

export class FeedbackStore {
  readonly #gateway: FeedbackGateway
  readonly #listeners = new Set<() => void>()
  readonly #submittedLog: SubmittedFeedbackLog
  #snapshot: FeedbackSnapshot
  #submissions: Map<MessageId, FeedbackSubmission>
  readonly #subscriptions: readonly Unsubscribe[]

  constructor(options: FeedbackStoreOptions) {
    this.#gateway = options.gateway
    this.#submittedLog = options.submittedLog
    this.#submissions = hydratedSubmissions(options.submittedLog)
    this.#snapshot = { submissions: this.#submissions }
    this.#subscriptions = [
      options.gateway.onAck((ack) => this.#handleAck(ack.messageId)),
      options.gateway.onError((error) => this.#handleError(error.messageId, error)),
    ]
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    return () => this.#listeners.delete(listener)
  }

  getSnapshot = (): FeedbackSnapshot => this.#snapshot

  openCorrectionForm(messageId: MessageId): void {
    const current = this.#current(messageId)
    const result = openCorrectionForm(current)

    if (isOk(result)) {
      this.#replace(result.value)
    }
  }

  cancelCorrectionForm(messageId: MessageId): void {
    const current = this.#current(messageId)
    const result = cancelCorrectionForm(current)

    if (isOk(result)) {
      this.#replace(result.value)
    }
  }

  ratePositive(messageId: MessageId): FeedbackActionResult {
    if (this.#submittedLog.all().has(messageId)) {
      return 'ignored'
    }

    const current = this.#current(messageId)
    const started = startPositiveSubmission(current)

    if (!isOk(started)) {
      return 'ignored'
    }

    return this.#sendOptimistically(started.value)
  }

  submitNegative(messageId: MessageId, draft: FeedbackDraft): FeedbackActionResult {
    if (this.#submittedLog.all().has(messageId)) {
      return 'ignored'
    }

    const current = this.#current(messageId)
    const started = startNegativeSubmission(current, draft)

    if (!isOk(started)) {
      return 'ignored'
    }

    return this.#sendOptimistically(started.value)
  }

  dispose(): void {
    for (const unsubscribe of this.#subscriptions) {
      unsubscribe()
    }
    this.#listeners.clear()
  }

  #sendOptimistically(submitting: FeedbackSubmission): FeedbackActionResult {
    const payload = toFeedbackPayload(submitting)
    if (payload === null) {
      return 'ignored'
    }

    this.#submittedLog.mark(submitting.messageId)
    this.#replace(submitting)

    const outcome = this.#gateway.submit(payload)
    if (outcome === 'dropped-closed') {
      this.#handleError(submitting.messageId, {
        code: 'connection_closed',
        message: FEEDBACK_COPY.errors.connection,
        messageId: submitting.messageId,
      })
    }

    return outcome
  }

  #handleAck(messageId: MessageId): void {
    const current = this.#submissions.get(messageId)

    if (current === undefined || current.status !== 'submitting' || !this.#submittedLog.all().has(messageId)) {
      return
    }

    const submitted = markFeedbackSubmitted(current)
    if (isOk(submitted)) {
      this.#replace(submitted.value)
    }
  }

  #handleError(messageId: MessageId | null, failure: FeedbackGatewayError): void {
    if (messageId === null) {
      return
    }

    const current = this.#submissions.get(messageId)
    if (current === undefined || (current.status !== 'submitted' && current.status !== 'submitting')) {
      return
    }

    const reverted = revertFeedback(current, mapFeedbackError(failure))
    if (!isOk(reverted)) {
      return
    }

    this.#submittedLog.unmark(messageId)
    this.#replace(reverted.value)
  }

  #current(messageId: MessageId): FeedbackSubmission {
    return this.#submissions.get(messageId) ?? createFeedbackSubmission(messageId)
  }

  #replace(submission: FeedbackSubmission): void {
    this.#submissions = new Map(this.#submissions)
    this.#submissions.set(submission.messageId, submission)
    this.#snapshot = { submissions: this.#submissions }

    for (const listener of this.#listeners) {
      listener()
    }
  }
}