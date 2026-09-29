import { conversationId } from '@/shared/kernel/branded'
import { Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'
import type { MessageBus, ServerFrame } from '@/shared/ws/message-bus'
import type { SendResult, SocketClient, Unsubscribe } from '@/shared/ws/socket-client'

import type {
  ConversationGateway,
  RosterPage,
} from '@/domains/conversation/application/ports'
import type {
  ConversationEvent,
  ConversationSummary,
  LoadedMessage,
} from '@/domains/conversation/domain/events'

type SocketWriter = Pick<SocketClient, 'send'>
type FrameSource = Pick<MessageBus, 'subscribe'>

type LoadedMessageFrame = Extract<ServerFrame, { type: 'conversation_loaded' }>['messages'][number]
type SummaryFrame = Extract<ServerFrame, { type: 'conversation_list' }>['conversations'][number]

// The frame's `updated_at` is server-local ISO; an unparseable one would otherwise sort the thread
// to an arbitrary position, so it falls back to the epoch and lands at the bottom of the list.
const instantOf = (iso: string): Instant => {
  const parsed = Instant.fromIso(iso)

  return isOk(parsed) ? parsed.value : Instant.EPOCH
}

// FR-CONV-014 is a presentation concern, so an empty server title is normalised to null here rather
// than to the fallback copy.
const titleOf = (title: string): string | null => (title.trim() === '' ? null : title)

const toSummary = (summary: SummaryFrame): ConversationSummary => ({
  id: conversationId(summary.id),
  title: titleOf(summary.title),
  updatedAt: instantOf(summary.updated_at),
  messageCount: summary.message_count,
})

// P8 / FR-CONV-009: only the role and how many tool calls a message carries decide whether the
// server is still working, so nothing else about the message crosses into `conv`.
const toLoadedMessage = (message: LoadedMessageFrame): LoadedMessage => ({
  role: message.role ?? '',
  toolCallCount: message.tool_calls.length,
})

const toConversationEvent = (frame: ServerFrame): ConversationEvent | null => {
  switch (frame.type) {
    case 'conversation_created':
      return { kind: 'created', id: conversationId(frame.conversation_id) }
    case 'conversation_titled':
      return { kind: 'titled', id: conversationId(frame.conversation_id), title: frame.title }
    case 'conversation_list':
      return { kind: 'listed', items: frame.conversations.map(toSummary) }
    case 'conversation_loaded':
      return {
        kind: 'loaded',
        id: conversationId(frame.conversation_id),
        messages: frame.messages.map(toLoadedMessage),
      }
    case 'conversation_renamed':
      return { kind: 'renamed', id: conversationId(frame.conversation_id), title: frame.title }
    case 'conversation_deleted':
      return { kind: 'deleted', id: conversationId(frame.conversation_id) }
    case 'conversation_bulk_deleted':
      return {
        kind: 'bulk-deleted',
        deletedIds: frame.deleted_ids.map(conversationId),
        activeDeleted: frame.active_conversation_deleted,
      }
    case 'conversation_all_deleted':
      return { kind: 'all-deleted', count: frame.deleted_count }
    case 'conversation_error':
      return {
        kind: 'error',
        code: frame.code,
        message: frame.message,
        id: frame.conversation_id === undefined ? null : conversationId(frame.conversation_id),
      }
    default:
      // Every frame owned by another context (CONTRACT-001 §2.4) is not this gateway's business.
      return null
  }
}

// CONTRACT-001 §2.2.
export class WsConversationGateway implements ConversationGateway {
  readonly #frames: FrameSource
  readonly #socket: SocketWriter

  constructor(socket: SocketWriter, frames: FrameSource) {
    this.#socket = socket
    this.#frames = frames
  }

  requestRoster(page: RosterPage = {}): SendResult {
    return this.#write({
      type: 'conversation_list',
      ...(page.limit === undefined ? {} : { limit: page.limit }),
      ...(page.offset === undefined ? {} : { offset: page.offset }),
    })
  }

  // P3: no reply frame. The id arrives later as `conversation_created` or on `ai_response`.
  create(): SendResult {
    return this.#write({ type: 'conversation_new' })
  }

  load(id: string): SendResult {
    return this.#write({ type: 'conversation_load', conversation_id: id })
  }

  rename(id: string, title: string): SendResult {
    return this.#write({ type: 'conversation_rename', conversation_id: id, title })
  }

  remove(id: string): SendResult {
    return this.#write({ type: 'conversation_delete', conversation_id: id })
  }

  removeMany(ids: readonly string[]): SendResult {
    return this.#write({ type: 'conversation_delete_bulk', conversation_ids: [...ids] })
  }

  removeAll(): SendResult {
    return this.#write({ type: 'conversation_delete_all' })
  }

  onEvent(callback: (event: ConversationEvent) => void): Unsubscribe {
    return this.#frames.subscribe((frame) => {
      const event = toConversationEvent(frame)

      if (event !== null) {
        callback(event)
      }
    })
  }

  #write(frame: object): SendResult {
    return this.#socket.send(JSON.stringify(frame))
  }
}
