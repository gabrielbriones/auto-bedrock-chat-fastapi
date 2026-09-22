import { createMessage, type ChatMessage } from '@/domains/messaging/domain/message'
import { isUnresolved, type Turn, type TurnActivity } from '@/domains/messaging/domain/turn'

// ADR-013: transient notices are their own collection, never fake `Message` entities, so loading a
// conversation drops them instead of persisting "Connection error occurred" into a transcript.
export type TransientMessage = {
  readonly id: string
  readonly message: ChatMessage
}

/** A persisted message as loaded from the server, with the tool activity behind an answer. */
export type HistoryMessage = {
  readonly message: ChatMessage
  readonly activity: TurnActivity | null
}

export type TranscriptEntry = {
  readonly key: string
  readonly message: ChatMessage
  /** The turn this entry belongs to is still awaiting a response. */
  readonly pending: boolean
  readonly streaming: boolean
  readonly transient: boolean
  /** ADR-008: a tool-progress notice, not assistant content — never markdown, never persisted. */
  readonly progress: boolean
  /** The turn was recycled by a drop or staleness; whatever streamed is kept and marked as partial. */
  readonly interrupted: boolean
  /** Final response provenance and context-loss metadata, shown with that response only. */
  readonly activity: TurnActivity | null
}

// Sorts immediately after the request it streams for and before whatever a later turn contributes;
// turn seqs are integers spaced at least one apart, so this fractional offset never collides.
const STREAMING_SEQ_OFFSET = 0.5

const byTimeThenSeq = (left: TranscriptEntry, right: TranscriptEntry): number =>
  left.message.at.compare(right.message.at) || left.message.seq - right.message.seq

// FR-MSG-006/006a: the streaming preview is neither the request nor the final `response` — it is
// a derived, replaced-in-place view of the turn's in-flight snapshot or tool-progress text,
// rendered once the first `typing` frame moves the turn into `streaming`.
const activityEntry = (turn: Turn): TranscriptEntry => ({
  key: `${turn.id}:activity`,
  message: createMessage(
    'assistant',
    turn.toolProgress ?? turn.streamText ?? '',
    turn.request.at,
    turn.request.seq + STREAMING_SEQ_OFFSET,
  ),
  pending: true,
  streaming: turn.streamText !== null,
  transient: false,
  progress: turn.toolProgress !== null,
  interrupted: false,
  activity: null,
})

const turnEntries = (turn: Turn): TranscriptEntry[] => {
  const streaming = turn.status === 'streaming'
  const entries: TranscriptEntry[] = [
    {
      key: `${turn.id}:request`,
      message: turn.request,
      pending: isUnresolved(turn),
      streaming,
      transient: false,
      progress: false,
      interrupted: false,
      activity: null,
    },
  ]

  if (streaming) {
    entries.push(activityEntry(turn))
  }

  // FR-MSG-005: a turn the connection killed stays visible as an interruption rather than a request
  // that was silently never answered, and keeps whatever it had already streamed.
  if (turn.abandonReason === 'connection-lost') {
    entries.push({
      key: `${turn.id}:interrupted`,
      message: createMessage(
        'assistant',
        turn.streamText ?? '',
        turn.request.at,
        turn.request.seq + STREAMING_SEQ_OFFSET,
      ),
      pending: false,
      streaming: false,
      transient: false,
      progress: false,
      interrupted: true,
      activity: null,
    })
  }

  if (turn.response !== null) {
    entries.push({
      key: `${turn.id}:response`,
      message: turn.response,
      pending: false,
      streaming: false,
      transient: false,
      progress: false,
      interrupted: false,
      activity: turn.activity,
    })
  }

  return entries
}

const historyEntry = ({ message, activity }: HistoryMessage): TranscriptEntry => ({
  key: `history:${message.id ?? message.seq}`,
  message,
  pending: false,
  streaming: false,
  transient: false,
  progress: false,
  interrupted: false,
  activity,
})

// SPEC-012 §1: the transcript is derived, never stored. Persisted messages are joined with any
// turns created after the conversation was loaded and with transient notices for this session.
// History keeps the server's order — the checkpoint stamps only assistant messages, so sorting it by
// time would put every unstamped question after every answer. Everything else postdates the load.
export const renderTranscript = (
  turns: readonly Turn[],
  transient: readonly TransientMessage[],
  history: readonly HistoryMessage[] = [],
): readonly TranscriptEntry[] => {
  const live = turns.flatMap(turnEntries)

  for (const notice of transient) {
    live.push({
      key: notice.id,
      message: notice.message,
      pending: false,
      streaming: false,
      transient: true,
      progress: false,
      interrupted: false,
      activity: null,
    })
  }

  return [...history.map(historyEntry), ...live.sort(byTimeThenSeq)]
}
