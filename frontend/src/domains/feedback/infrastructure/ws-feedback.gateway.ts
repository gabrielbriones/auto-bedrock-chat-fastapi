import { messageId } from '@/shared/kernel/branded'
import type { MessageBus, ServerFrame } from '@/shared/ws/message-bus'
import type { SendResult, SocketClient, Unsubscribe } from '@/shared/ws/socket-client'

import type {
  FeedbackAck,
  FeedbackGateway,
  FeedbackGatewayError,
} from '@/domains/feedback/application/ports'
import type { FeedbackPayload } from '@/domains/feedback/domain/feedback-submission'

type SocketWriter = Pick<SocketClient, 'send'>
type FrameSource = Pick<MessageBus, 'subscribe'>

type FeedbackEvent =
  | { readonly kind: 'ack'; readonly value: FeedbackAck }
  | { readonly kind: 'error'; readonly value: FeedbackGatewayError }

const toFeedbackEvent = (frame: ServerFrame): FeedbackEvent | null => {
  switch (frame.type) {
    case 'feedback_ack':
      return {
        kind: 'ack',
        value: {
          messageId: messageId(frame.message_id),
          feedbackId: frame.feedback_id,
          status: frame.status,
        },
      }
    case 'feedback_error':
      return {
        kind: 'error',
        value: {
          code: frame.code,
          message: frame.message,
          messageId: frame.message_id === undefined ? null : messageId(frame.message_id),
        },
      }
    default:
      return null
  }
}

export class WsFeedbackGateway implements FeedbackGateway {
  readonly #frames: FrameSource
  readonly #socket: SocketWriter

  constructor(socket: SocketWriter, frames: FrameSource) {
    this.#socket = socket
    this.#frames = frames
  }

  submit(payload: FeedbackPayload): SendResult {
    return this.#socket.send(JSON.stringify({ type: 'feedback', ...payload }))
  }

  onAck(callback: (ack: FeedbackAck) => void): Unsubscribe {
    return this.#frames.subscribe((frame) => {
      const event = toFeedbackEvent(frame)

      if (event?.kind === 'ack') {
        callback(event.value)
      }
    })
  }

  onError(callback: (error: FeedbackGatewayError) => void): Unsubscribe {
    return this.#frames.subscribe((frame) => {
      const event = toFeedbackEvent(frame)

      if (event?.kind === 'error') {
        callback(event.value)
      }
    })
  }
}