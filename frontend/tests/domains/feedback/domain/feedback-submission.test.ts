import { describe, expect, it } from 'vitest'

import { messageId } from '@/shared/kernel/branded'
import { isErr, isOk } from '@/shared/kernel/result'
import {
  cancelCorrectionForm,
  createFeedbackSubmission,
  markFeedbackSubmitted,
  openCorrectionForm,
  revertFeedback,
  startNegativeSubmission,
  startPositiveSubmission,
  toFeedbackPayload,
} from '@/domains/feedback/domain/feedback-submission'

const id = messageId('message-1')

describe('FeedbackSubmission', () => {
  it('moves a positive rating through submitting to submitted', () => {
    const started = startPositiveSubmission(createFeedbackSubmission(id))

    expect(isOk(started)).toBe(true)
    if (!isOk(started)) return
    expect(started.value.status).toBe('submitting')
    expect(started.value.rating).toBe('positive')

    const submitted = markFeedbackSubmitted(started.value)

    expect(isOk(submitted)).toBe(true)
    if (!isOk(submitted)) return
    expect(submitted.value.status).toBe('submitted')
  })

  it('opens and cancels a negative correction form without retaining its draft', () => {
    const opened = openCorrectionForm(createFeedbackSubmission(id))

    expect(isOk(opened)).toBe(true)
    if (!isOk(opened)) return

    const cancelled = cancelCorrectionForm(opened.value)

    expect(isOk(cancelled)).toBe(true)
    if (!isOk(cancelled)) return
    expect(cancelled.value).toEqual({
      messageId: id,
      rating: null,
      status: 'idle',
      error: null,
    })
  })

  it('trims optional fields and omits blanks from the payload', () => {
    const opened = openCorrectionForm(createFeedbackSubmission(id))
    if (!isOk(opened)) throw new Error('Expected correction form to open')

    const started = startNegativeSubmission(opened.value, {
      correctionText: '  Use the measured IPC.  ',
      userComment: '   ',
    })

    expect(isOk(started)).toBe(true)
    if (!isOk(started)) return
    expect(toFeedbackPayload(started.value)).toEqual({
      message_id: 'message-1',
      rating: 'negative',
      correction_text: 'Use the measured IPC.',
    })
  })

  it('restores a retryable failed state and permits a new rating', () => {
    const started = startPositiveSubmission(createFeedbackSubmission(id))
    if (!isOk(started)) throw new Error('Expected positive submission to start')

    const failed = revertFeedback(started.value, {
      code: 'unauthorized_feedback',
      message: 'You are not allowed to rate this message.',
    })

    expect(isOk(failed)).toBe(true)
    if (!isOk(failed)) return
    expect(failed.value.status).toBe('failed')
    expect(failed.value.error?.code).toBe('unauthorized_feedback')

    const retried = startPositiveSubmission(failed.value)

    expect(isOk(retried)).toBe(true)
    if (!isOk(retried)) return
    expect(retried.value.error).toBeNull()
  })

  it('rejects transitions that violate the state machine', () => {
    const idle = createFeedbackSubmission(id)
    const submitted = markFeedbackSubmitted(idle)
    const submitting = startPositiveSubmission(idle)

    expect(isErr(submitted)).toBe(true)
    expect(isErr(startNegativeSubmission(idle, {}))).toBe(true)
    expect(isErr(cancelCorrectionForm(idle))).toBe(true)
    expect(isErr(openCorrectionForm(isOk(submitting) ? submitting.value : idle))).toBe(true)
  })
})