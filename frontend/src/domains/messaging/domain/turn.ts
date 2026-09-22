import type { ConversationId, TurnId } from '@/shared/kernel/branded'
import { err, ok, type Result } from '@/shared/kernel/result'

import type { ChatMessage } from '@/domains/messaging/domain/message'

// DESIGN-001 §3.1. The typing frame enters `streaming`; T-071 applies its cumulative text.
export type TurnStatus = 'composing' | 'sent' | 'streaming' | 'answered' | 'failed' | 'abandoned'

// ADR-008: `typing.message` is cumulative, never a delta — applying it always replaces.
export type StreamingSnapshotKind = 'text' | 'tool-progress'

export type ToolCall = {
  readonly id: string
  readonly name: string
  readonly arguments: unknown
}

export type ToolResult = {
  readonly toolCallId: string | null
  readonly name: string
  readonly result: unknown
  readonly error: unknown | null
}

export type KbCitation = {
  readonly documentId: string | null
  readonly title: string | null
  readonly source: string | null
  readonly url: string | null
  readonly score: number
}

export type TurnActivity = {
  readonly toolCalls: readonly ToolCall[]
  readonly toolResults: readonly ToolResult[]
  readonly citations: readonly KbCitation[]
  /** The server condensed older context before answering this turn. */
  readonly truncated: boolean
}

const NO_ACTIVITY: TurnActivity = {
  toolCalls: [],
  toolResults: [],
  citations: [],
  truncated: false,
}

// A turn can end without an answer two ways, and they read differently to the user: the frame never
// left, or it left and the connection died before the answer landed.
export type AbandonReason = 'never-sent' | 'connection-lost'

export type Turn = {
  readonly id: TurnId
  readonly conversationId: ConversationId | null
  readonly request: ChatMessage
  readonly response: ChatMessage | null
  readonly status: TurnStatus
  /** The cumulative assistant snapshot so far (FR-MSG-006). Never the final `response`. */
  readonly streamText: string | null
  /** The latest tool-progress string (ADR-008), rendered instead of `streamText` while present. */
  readonly toolProgress: string | null
  /** Why the turn was abandoned; `null` for every other status. */
  readonly abandonReason: AbandonReason | null
  /** Final response provenance and context-loss notice data (FR-MSG-015...023). */
  readonly activity: TurnActivity
}

export type IllegalTransition = {
  readonly kind: 'illegal-transition'
  readonly from: TurnStatus
  readonly to: TurnStatus
}

export type TurnResult = Result<Turn, IllegalTransition>

const UNRESOLVED: readonly TurnStatus[] = ['sent', 'streaming']

// I3: one-way. Every resolution is only reachable from an unresolved turn, so a late duplicate
// frame cannot revive or re-answer a turn that already settled.
const transition = (turn: Turn, to: TurnStatus, next: Omit<Turn, 'status'>): TurnResult =>
  UNRESOLVED.includes(turn.status)
    ? ok({ ...next, status: to })
    : err({ kind: 'illegal-transition', from: turn.status, to })

export const createTurn = (
  id: TurnId,
  request: ChatMessage,
  conversationId: ConversationId | null = null,
): Turn => ({
  id,
  conversationId,
  request,
  response: null,
  status: 'sent',
  streamText: null,
  toolProgress: null,
  abandonReason: null,
  activity: NO_ACTIVITY,
})

export const isUnresolved = (turn: Turn): boolean => UNRESOLVED.includes(turn.status)

// ADR-008: a `typing` frame matching "Calling {fn}... (n/total)" reports tool progress, not
// assistant content (FR-MSG-006). Everything else is streamed text.
const TOOL_PROGRESS_PATTERN = /^Calling .+\.\.\. \(\d+\/\d+\)$/

export const classifyTypingFrame = (text: string): StreamingSnapshotKind =>
  TOOL_PROGRESS_PATTERN.test(text) ? 'tool-progress' : 'text'

// I4: rejected outside {sent, streaming}. Text snapshots are cumulative (ADR-008): a snapshot
// shorter than what is already applied is a duplicate or an out-of-order replay and is dropped,
// so the final rendered text never regresses regardless of delivery order. Tool-progress strings
// are not cumulative — each is its own point-in-time status and always replaces the last one.
export const applyStreamingSnapshot = (
  turn: Turn,
  text: string,
  kind: StreamingSnapshotKind,
): TurnResult => {
  if (kind === 'tool-progress') {
    return transition(turn, 'streaming', { ...turn, toolProgress: text })
  }

  const current = turn.streamText ?? ''
  const streamText = text.length >= current.length ? text : current

  // A text frame supersedes any tool-progress notice: the assistant is producing an answer again.
  return transition(turn, 'streaming', { ...turn, streamText, toolProgress: null })
}

// I2: a response is attached exactly when the turn reaches `answered` or `failed`.
export const answerTurn = (
  turn: Turn,
  response: ChatMessage,
  conversationId: ConversationId | null = turn.conversationId,
  activity: TurnActivity = NO_ACTIVITY,
): TurnResult => transition(turn, 'answered', { ...turn, response, conversationId, activity })

export const failTurn = (turn: Turn, response: ChatMessage): TurnResult =>
  transition(turn, 'failed', { ...turn, response })

// FR-MSG-024: a socket close abandons the pending turn rather than leaving it awaiting forever.
export const abandonTurn = (
  turn: Turn,
  reason: AbandonReason = 'connection-lost',
): TurnResult => transition(turn, 'abandoned', { ...turn, response: null, abandonReason: reason })
