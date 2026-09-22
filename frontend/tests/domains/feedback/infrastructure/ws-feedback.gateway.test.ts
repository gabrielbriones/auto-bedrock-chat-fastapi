import { describe, expect, it, vi } from 'vitest'

import type { ServerFrame, ServerFrameSubscriber } from '@/shared/ws/message-bus'

import { messageId } from '@/shared/kernel/branded'
import type { FeedbackAck, FeedbackGatewayError } from '@/domains/feedback/application/ports'
import { WsFeedbackGateway } from '@/domains/feedback/infrastructure/ws-feedback.gateway'

const TIMESTAMP = '2026-08-25T12:00:00Z'

const createHarness = () => {
  const send = vi.fn<(frame: string) => 'sent' | 'dropped-closed'>(() => 'sent')
  const subscribers = new Set<ServerFrameSubscriber>()
  const subscribe = vi.fn((next: ServerFrameSubscriber) => {
    subscribers.add(next)
    return () => subscribers.delete(next)
  })
  const gateway = new WsFeedbackGateway({ send }, { subscribe })
  const acks: FeedbackAck[] = []
  const errors: FeedbackGatewayError[] = []
  gateway.onAck((ack) => acks.push(ack))
  gateway.onError((error) => errors.push(error))

  return {
    acks,
    errors,
    gateway,
    send,
    emit(frame: ServerFrame) {
      for (const subscriber of subscribers) {
        subscriber(frame)
      }
    },
  }
}

describe('WsFeedbackGateway', () => {
  it('serialises positive and negative feedback frames without blank fields', () => {
    const harness = createHarness()

    harness.gateway.submit({ message_id: 'm-1', rating: 'positive' })
    harness.gateway.submit({
      message_id: 'm-2',
      rating: 'negative',
      correction_text: 'Use measured IPC.',
      user_comment: 'Helpful context',
    })

    expect(harness.send.mock.calls.map(([frame]) => JSON.parse(frame))).toEqual([
      { type: 'feedback', message_id: 'm-1', rating: 'positive' },
      {
        type: 'feedback',
        message_id: 'm-2',
        rating: 'negative',
        correction_text: 'Use measured IPC.',
        user_comment: 'Helpful context',
      },
    ])
  })

  it('maps the recorded acknowledgement and error frames', () => {
    const harness = createHarness()

    harness.emit({
      type: 'feedback_ack',
      timestamp: TIMESTAMP,
      message_id: 'm-1',
      feedback_id: 'feedback-1',
      status: 'recorded',
    })
    harness.emit({
      type: 'feedback_error',
      timestamp: TIMESTAMP,
      code: 'unauthorized_feedback',
      message: 'Not allowed',
      message_id: 'm-1',
    })

    expect(harness.acks).toEqual([
      { messageId: messageId('m-1'), feedbackId: 'feedback-1', status: 'recorded' },
    ])
    expect(harness.errors).toEqual([
      {
        code: 'unauthorized_feedback',
        message: 'Not allowed',
        messageId: messageId('m-1'),
      },
    ])
  })

  it('keeps an error without a message id representable for the store to ignore', () => {
    const harness = createHarness()

    harness.emit({
      type: 'feedback_error',
      timestamp: TIMESTAMP,
      code: 'feedback_unavailable',
      message: 'Unavailable',
    })

    expect(harness.errors).toEqual([
      { code: 'feedback_unavailable', message: 'Unavailable', messageId: null },
    ])
  })

  it('reports a closed socket without queueing', () => {
    const harness = createHarness()
    harness.send.mockReturnValue('dropped-closed')

    expect(harness.gateway.submit({ message_id: 'm-1', rating: 'positive' })).toBe('dropped-closed')
  })
})