import { describe, expect, it } from 'vitest'

import { messageId } from '@/shared/kernel/branded'

import type {
  FeedbackAck,
  FeedbackGateway,
  FeedbackGatewayError,
  SubmittedFeedbackLog,
} from '@/domains/feedback/application/ports'
import { FeedbackStore } from '@/domains/feedback/application/feedback.store'

const createHarness = (sendResult: 'sent' | 'dropped-closed' = 'sent') => {
  const submitted = new Set<ReturnType<typeof messageId>>()
  const ackListeners = new Set<(ack: FeedbackAck) => void>()
  const errorListeners = new Set<(error: FeedbackGatewayError) => void>()
  const sent: Record<string, unknown>[] = []
  const gateway: FeedbackGateway = {
    submit(payload) {
      sent.push(payload)
      return sendResult
    },
    onAck(listener) {
      ackListeners.add(listener)
      return () => ackListeners.delete(listener)
    },
    onError(listener) {
      errorListeners.add(listener)
      return () => errorListeners.delete(listener)
    },
  }
  const log: SubmittedFeedbackLog = {
    all: () => submitted,
    mark: (id) => submitted.add(id),
    unmark: (id) => submitted.delete(id),
  }
  const store = new FeedbackStore({ gateway, submittedLog: log })

  return {
    log: submitted,
    submittedLog: log,
    sent,
    store,
    emitAck(ack: FeedbackAck) {
      for (const listener of ackListeners) listener(ack)
    },
    emitError(error: FeedbackGatewayError) {
      for (const listener of errorListeners) listener(error)
    },
  }
}

const stateOf = (harness: ReturnType<typeof createHarness>, id: ReturnType<typeof messageId>) =>
  harness.store.getSnapshot().submissions.get(id)

describe('FeedbackStore', () => {
  it('marks a positive submission optimistically and enforces idempotence', () => {
    const harness = createHarness()
    const id = messageId('m-1')

    expect(harness.store.ratePositive(id)).toBe('sent')
    expect(stateOf(harness, id)).toMatchObject({ status: 'submitting', rating: 'positive' })
    expect(harness.log.has(id)).toBe(true)
    expect(harness.sent).toEqual([{ message_id: 'm-1', rating: 'positive' }])

    expect(harness.store.ratePositive(id)).toBe('ignored')
    expect(harness.sent).toHaveLength(1)

    harness.emitAck({ messageId: id, feedbackId: 'f-1', status: 'recorded' })
    expect(stateOf(harness, id)?.status).toBe('submitted')
  })

  it('restores controls with a local connection error when sending drops', () => {
    const harness = createHarness('dropped-closed')
    const id = messageId('m-1')

    expect(harness.store.ratePositive(id)).toBe('dropped-closed')
    expect(stateOf(harness, id)).toMatchObject({
      status: 'failed',
      error: { code: 'connection_closed', message: 'Connection unavailable. Please try again.' },
    })
    expect(harness.log.has(id)).toBe(false)
  })

  it('trims negative fields and rolls back with mapped server copy', () => {
    const harness = createHarness()
    const id = messageId('m-1')

    harness.store.openCorrectionForm(id)
    expect(stateOf(harness, id)?.status).toBe('drafting')
    expect(
      harness.store.submitNegative(id, {
        correctionText: '  Correct answer  ',
        userComment: '   ',
      }),
    ).toBe('sent')

    expect(harness.sent).toEqual([
      { message_id: 'm-1', rating: 'negative', correction_text: 'Correct answer' },
    ])
    expect(stateOf(harness, id)?.status).toBe('submitting')

    harness.emitError({
      code: 'unauthorized_feedback',
      message: 'server detail',
      messageId: id,
    })

    expect(stateOf(harness, id)).toMatchObject({
      status: 'failed',
      error: { message: 'You are not allowed to rate this message.' },
    })
    expect(harness.log.has(id)).toBe(false)
  })

  it('hydrates a submitted id and ignores an acknowledgement for an unknown id', () => {
    const harness = createHarness()
    const storedId = messageId('stored')
    harness.log.add(storedId)
    const hydrated = new FeedbackStore({
      gateway: {
        submit: () => 'sent',
        onAck: (listener) => {
          listener({ messageId: messageId('unknown'), feedbackId: 'f-1', status: 'recorded' })
          return () => {}
        },
        onError: () => () => {},
      },
      submittedLog: harness.submittedLog,
    })

    hydrated.getSnapshot()
    expect(hydrated.getSnapshot().submissions.get(storedId)?.status).toBe('submitted')
    expect(harness.store.getSnapshot().submissions.has(messageId('unknown'))).toBe(false)
  })
})