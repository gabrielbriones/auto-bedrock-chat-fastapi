import type { MessageBus, ServerFrame } from '@/shared/ws/message-bus'
import type { SendResult, SocketClient, Unsubscribe } from '@/shared/ws/socket-client'
import { Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

import type {
  LoadedHistoryMessage,
  MessagingEvent,
  MessagingGateway,
} from '@/domains/messaging/application/ports'
import type { KbCitation, ToolCall, ToolResult, TurnActivity } from '@/domains/messaging/domain/turn'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

type SocketWriter = Pick<SocketClient, 'send'>
type FrameSource = Pick<MessageBus, 'subscribe'>
type HistoryFilter = (conversationId: string) => boolean

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const stringOr = (value: unknown, fallback: string): string =>
  typeof value === 'string' && value.length > 0 ? value : fallback

type HistoryFrameMessage = Extract<ServerFrame, { type: 'conversation_loaded' }>['messages'][number]

const parseInstant = (timestamp: string | null | undefined): Instant | null => {
  if (timestamp === null || timestamp === undefined) {
    return null
  }

  const parsed = Instant.fromIso(timestamp)
  return isOk(parsed) ? parsed.value : null
}

// The checkpoint stamps assistant messages only. A user message borrows the time of the answer that
// followed it, so it never ends up "newer" than the whole transcript just because it was unstamped.
const historyInstants = (messages: readonly HistoryFrameMessage[], fallback: string): Instant[] => {
  const known = messages.map((message) => parseInstant(message.timestamp))
  const frameInstant = parseInstant(fallback) ?? Instant.EPOCH
  const resolved: Instant[] = []

  for (let index = 0; index < known.length; index += 1) {
    const own = known[index]
    if (own !== null && own !== undefined) {
      resolved.push(own)
      continue
    }

    const later = known.slice(index + 1).find((instant) => instant !== null)
    resolved.push(later ?? resolved.at(-1) ?? frameInstant)
  }

  return resolved
}

const emptyActivity = (): TurnActivity => ({ toolCalls: [], toolResults: [], citations: [], truncated: false })

const hasActivity = (activity: TurnActivity): boolean =>
  activity.toolCalls.length > 0 || activity.toolResults.length > 0

// The checkpoint is the graph's raw message list: the system prompt, then per turn a user message,
// zero or more `assistant`(tool_calls) / `tool`(results) pairs, and the answering assistant message.
// Only user and assistant text is transcript content (as the legacy client also rendered); tool rounds
// fold into the answer's activity, the same shape the live `ai_response` frame carries.
const historyMessages = (
  messages: readonly HistoryFrameMessage[],
  fallbackTimestamp: string,
): readonly LoadedHistoryMessage[] => {
  const instants = historyInstants(messages, fallbackTimestamp)
  const loaded: LoadedHistoryMessage[] = []
  let pending = emptyActivity()

  const flushPending = (at: Instant): void => {
    if (hasActivity(pending)) {
      loaded.push({ id: null, role: 'assistant', text: '', at, activity: pending })
    }
    pending = emptyActivity()
  }

  messages.forEach((message, index) => {
    const at = instants[index] ?? Instant.EPOCH

    switch (message.role) {
      case 'user':
        flushPending(at)
        loaded.push({ id: message.message_id ?? null, role: 'user', text: message.content, at, activity: null })
        return
      case 'tool':
        pending = { ...pending, toolResults: [...pending.toolResults, ...toolResults(message.tool_results)] }
        return
      case 'assistant': {
        const calls = toolCalls(message.tool_calls)
        if (calls.length > 0) {
          pending = { ...pending, toolCalls: [...pending.toolCalls, ...calls] }
          return
        }

        loaded.push({
          id: message.message_id ?? null,
          role: 'assistant',
          text: message.content,
          at,
          activity: hasActivity(pending) ? pending : null,
        })
        pending = emptyActivity()
        return
      }
      default:
        // `system` is the graph's own prompt, never something the user said or was told.
        return
    }
  })

  flushPending(instants.at(-1) ?? Instant.EPOCH)
  return loaded
}

const toolCalls = (values: readonly unknown[]): readonly ToolCall[] =>
  values.map((value, index) => {
    const call = isRecord(value) ? value : {}
    return {
      id: stringOr(call.id, `tool-call-${index + 1}`),
      name: stringOr(call.name, MESSAGING_COPY.transcript.unknownTool),
      arguments: call.args ?? call.arguments ?? {},
    }
  })

const toolResults = (values: readonly unknown[]): readonly ToolResult[] =>
  values.map((value, index) => {
    const result = isRecord(value) ? value : {}
    return {
      toolCallId: typeof result.tool_call_id === 'string' ? result.tool_call_id : null,
      name: stringOr(result.name, MESSAGING_COPY.transcript.toolResult(index + 1)),
      result: result.result ?? result.content ?? null,
      error: result.error ?? null,
    }
  })

const citations = (
  values: readonly {
    readonly document_id: string | null
    readonly title: string | null
    readonly source: string | null
    readonly url: string | null
    readonly score: number
  }[],
): readonly KbCitation[] =>
  values.map((source) => ({
    documentId: source.document_id,
    title: source.title,
    source: source.source,
    url: source.url,
    score: source.score,
  }))

// CONTRACT-001 §2.2. Overrides and the heartbeat join this frame in Phase 4 (`T-080`).
const toChatFrame = (text: string): object => ({ type: 'chat', message: text })

const toMessagingEvent = (frame: ServerFrame): MessagingEvent | null => {
  switch (frame.type) {
    case 'connection_established':
      return { kind: 'session-established', sessionId: frame.session_id }
    case 'typing':
      return { kind: 'typing', text: frame.message }
    case 'ai_response':
      // The error variant carries only `message`; everything else is absent by contract.
      return frame.error === true
        ? { kind: 'failed', text: frame.message }
        : {
            kind: 'answered',
            text: frame.message,
            messageId: frame.message_id ?? null,
            conversationId: frame.conversation_id ?? null,
            toolCalls: toolCalls(frame.tool_calls ?? []),
            toolResults: toolResults(frame.tool_results ?? []),
            citations: citations(frame.metadata?.kb_sources ?? []),
            truncated: frame.metadata?.preprocessing_applied === true,
            configuredModel: {
              id: frame.metadata?.model_id ?? '',
              name: frame.metadata?.model_name ?? frame.metadata?.model_id ?? '',
            },
          }
    case 'error':
      return { kind: 'failed', text: frame.message }
    case 'conversation_loaded':
      return {
        kind: 'history-loaded',
        conversationId: frame.conversation_id,
        messages: historyMessages(frame.messages, frame.timestamp),
      }
    default:
      // Frames this context owns but does not use yet (`pong`, `history`, `history_cleared`) and
      // every frame owned by another context are simply not this gateway's business.
      return null
  }
}

export class WsMessagingGateway implements MessagingGateway {
  readonly #acceptHistory: HistoryFilter
  readonly #frames: FrameSource
  readonly #socket: SocketWriter

  constructor(socket: SocketWriter, frames: FrameSource, acceptHistory: HistoryFilter = () => true) {
    this.#socket = socket
    this.#frames = frames
    this.#acceptHistory = acceptHistory
  }

  sendChat(text: string): SendResult {
    return this.#socket.send(JSON.stringify(toChatFrame(text)))
  }

  onEvent(callback: (event: MessagingEvent) => void): Unsubscribe {
    return this.#frames.subscribe((frame) => {
      if (frame.type === 'conversation_loaded' && !this.#acceptHistory(frame.conversation_id)) {
        return
      }

      const event = toMessagingEvent(frame)

      if (event !== null) {
        callback(event)
      }
    })
  }
}
