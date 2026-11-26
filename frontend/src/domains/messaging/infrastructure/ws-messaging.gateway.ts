import type { MessageBus, ServerFrame } from '@/shared/ws/message-bus'
import type { SendResult, SocketClient, Unsubscribe } from '@/shared/ws/socket-client'
import { USAGE_PURPOSES, usageSchema, type WireUsage } from '@/shared/ws/usage-frames'
import { Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

import type {
  LoadedHistoryMessage,
  MessagingEvent,
  MessagingGateway,
} from '@/domains/messaging/application/ports'
import type { KbCitation, ToolCall, ToolResult, TurnActivity, TurnUsage } from '@/domains/messaging/domain/turn'
import { createTokenCount } from '@/domains/telemetry/domain/public'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

type SocketWriter = Pick<SocketClient, 'send'>
type FrameSource = Pick<MessageBus, 'subscribe'>
type ConversationFilter = (conversationId: string) => boolean

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

const emptyActivity = (): TurnActivity => ({ toolCalls: [], toolResults: [], citations: [], truncated: false, usage: null })

const hasActivity = (activity: TurnActivity): boolean =>
  activity.toolCalls.length > 0 || activity.toolResults.length > 0

// Two wire shapes share `usage`: the turn summary (`cache_read`, `final`, ...) and, when no ledger ran,
// the last call's own counts (`cache_read_tokens`). The latter has no `final`: nothing follows it.
const turnUsage = (usage: WireUsage | undefined): TurnUsage | null => {
  if (usage === undefined) {
    return null
  }

  const input = usage.input_tokens ?? null
  const output = usage.output_tokens ?? null
  if (input === null && output === null) {
    return null
  }

  return {
    tokens: createTokenCount(
      input ?? 0,
      output ?? 0,
      usage.cache_read ?? usage.cache_read_tokens ?? 0,
      usage.cache_write ?? usage.cache_write_tokens ?? 0,
    ),
    byPurpose: USAGE_PURPOSES.flatMap((purpose) => {
      const totals = usage.by_purpose?.[purpose]
      return totals === undefined ? [] : [{ purpose, tokens: createTokenCount(totals.input_tokens, totals.output_tokens) }]
    }),
    estimatedCost: usage.estimated_cost ?? null,
    currency: usage.currency ?? null,
    final: usage.final ?? true,
    context:
      usage.context === undefined ? null : { usedTokens: usage.context.used_tokens, maxTokens: usage.context.max_tokens },
  }
}

// History metadata is an untyped record, so its usage is validated here rather than by the bus.
const historyUsage = (metadata: Record<string, unknown>): TurnUsage | null => {
  const parsed = usageSchema.safeParse(metadata.usage)
  return parsed.success ? turnUsage(parsed.data) : null
}

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

        // XMGPLAT-11977: a turn the server closed as interrupted shows as a notice, not its tool round.
        const interrupted = message.metadata.interrupted === true
        const usage = interrupted ? null : historyUsage(message.metadata)
        const keepActivity = !interrupted && (usage !== null || hasActivity(pending))
        loaded.push({
          id: message.message_id ?? null,
          role: 'assistant',
          text: interrupted ? '' : message.content,
          at,
          activity: keepActivity ? { ...pending, usage } : null,
          ...(interrupted ? { interrupted } : {}),
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

type ToolFrame = Extract<ServerFrame, { type: 'tool_call_start' | 'tool_call_end' }>

const toolEvent = (frame: ToolFrame): MessagingEvent | null => {
  if (frame.type === 'tool_call_start') {
    return {
      kind: 'tool-started',
      id: frame.tool_call_id ?? `tool-call-${frame.position}`,
      name: stringOr(frame.name, MESSAGING_COPY.transcript.unknownTool),
      arguments: frame.arguments ?? {},
    }
  }

  // An end without an id cannot be matched to the row its start created.
  return frame.tool_call_id === null
    ? null
    : {
        kind: 'tool-finished',
        id: frame.tool_call_id,
        error: frame.status === 'error' ? (frame.error ?? MESSAGING_COPY.transcript.failed) : null,
        durationMs: frame.duration_ms ?? null,
      }
}

// CONTRACT-001 §2.2. Overrides and the heartbeat join this frame in Phase 4 (`T-080`).
const toChatFrame = (text: string): object => ({ type: 'chat', message: text })

const toMessagingEvent = (frame: ServerFrame): MessagingEvent | null => {
  switch (frame.type) {
    case 'connection_established':
      return { kind: 'session-established', sessionId: frame.session_id }
    case 'typing':
      return { kind: 'typing', text: frame.message }
    case 'turn_started':
      return { kind: 'turn-observed', text: frame.message, conversationId: frame.conversation_id }
    case 'tool_call_start':
    case 'tool_call_end':
      return toolEvent(frame)
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
            usage: turnUsage(frame.metadata?.usage),
            configuredModel: {
              id: frame.metadata?.model_id ?? '',
              name: frame.metadata?.model_name ?? frame.metadata?.model_id ?? '',
            },
          }
    case 'error':
    case 'skill_error':
      return { kind: 'failed', text: frame.message }
    case 'usage_updated': {
      const usage = turnUsage(frame.usage)
      return usage === null ? null : { kind: 'usage-updated', messageId: frame.message_id, usage }
    }
    case 'conversation_loaded':
      return {
        kind: 'history-loaded',
        conversationId: frame.conversation_id,
        messages: historyMessages(frame.messages, frame.timestamp),
      }
    // The thread exists but its checkpoint is gone: for the transcript that is a load with nothing in it.
    case 'conversation_error':
      return frame.code === 'conversation_history_unavailable' && frame.conversation_id !== undefined
        ? { kind: 'history-loaded', conversationId: frame.conversation_id, messages: [] }
        : null
    default:
      // Frames this context owns but does not use yet (`pong`, `history`, `history_cleared`) and
      // every frame owned by another context are simply not this gateway's business.
      return null
  }
}

// The conversation a frame belongs to, for the frames that are scoped to one. A turn keeps running
// after the user switches away (XMGPLAT-11999), so its progress and answer are scoped like history.
const conversationOf = (frame: ServerFrame): string | null => {
  switch (frame.type) {
    case 'typing':
    case 'turn_started':
    case 'tool_call_start':
    case 'tool_call_end':
    case 'ai_response':
    case 'conversation_loaded':
    case 'conversation_error':
      return frame.conversation_id ?? null
    default:
      return null
  }
}

export class WsMessagingGateway implements MessagingGateway {
  readonly #acceptConversation: ConversationFilter
  readonly #frames: FrameSource
  readonly #socket: SocketWriter

  constructor(socket: SocketWriter, frames: FrameSource, acceptConversation: ConversationFilter = () => true) {
    this.#socket = socket
    this.#frames = frames
    this.#acceptConversation = acceptConversation
  }

  sendChat(text: string): SendResult {
    return this.#socket.send(JSON.stringify(toChatFrame(text)))
  }

  onEvent(callback: (event: MessagingEvent) => void): Unsubscribe {
    return this.#frames.subscribe((frame) => {
      const scope = conversationOf(frame)

      // The server still checkpoints a dropped answer; it shows when that conversation is reopened.
      if (scope !== null && !this.#acceptConversation(scope)) {
        return
      }

      const event = toMessagingEvent(frame)

      if (event !== null) {
        callback(event)
      }
    })
  }
}
