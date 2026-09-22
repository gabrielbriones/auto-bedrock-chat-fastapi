import { conversationId, messageId, turnId } from '@/shared/kernel/branded'
import type { Clock } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'
import type { ConnectionState, Unsubscribe } from '@/shared/ws/socket-client'

import { connectionView, type ConnectionView } from '@/domains/messaging/domain/connection'
import {
  createMessage,
  type ChatMessage,
  type MessageRole,
} from '@/domains/messaging/domain/message'
import {
  renderTranscript,
  type HistoryMessage,
  type TranscriptEntry,
  type TransientMessage,
} from '@/domains/messaging/domain/transcript'
import {
  abandonTurn,
  answerTurn,
  applyStreamingSnapshot,
  classifyTypingFrame,
  createTurn,
  failTurn,
  isUnresolved,
  type Turn,
  type TurnResult,
} from '@/domains/messaging/domain/turn'
import type {
  ConnectionSource,
  MessagingEvent,
  MessagingGateway,
} from '@/domains/messaging/application/ports'

export type ChatSessionSnapshot = {
  readonly connection: ConnectionView
  readonly sessionId: string | null
  readonly transcript: readonly TranscriptEntry[]
  readonly awaitingResponse: boolean
  readonly canSend: boolean
  readonly configuredModel: { readonly id: string; readonly name: string } | null
}

export type SendOutcome = 'sent' | 'empty' | 'refused' | 'dropped-closed'

export type ChatSessionStoreOptions = {
  readonly gateway: MessagingGateway
  readonly connection: ConnectionSource
  readonly clock: Clock
  readonly logger: Logger
  /** Client-generated turn ids (DESIGN-001 §3.1); injectable so tests stay deterministic. */
  readonly newId?: () => string
}

const defaultNewId = (): string => crypto.randomUUID()

// SPEC-012 §4. Owns the session read model: connection state, the turns created here, and the
// transient notices of ADR-013. Persisted history joins the merge in Phase 5.
export class ChatSessionStore {
  readonly #clock: Clock
  #connectionState: ConnectionState
  #configuredModel: ChatSessionSnapshot['configuredModel'] = null
  readonly #gateway: MessagingGateway
  #history: readonly HistoryMessage[] = []
  readonly #listeners = new Set<() => void>()
  readonly #logger: Logger
  readonly #newId: () => string
  #seq = 0
  #sessionId: string | null = null
  #snapshot: ChatSessionSnapshot
  #subscriptions: readonly Unsubscribe[] = []
  #transient: readonly TransientMessage[] = []
  #turns: readonly Turn[] = []
  #typingObserved = false

  constructor(options: ChatSessionStoreOptions) {
    this.#clock = options.clock
    this.#gateway = options.gateway
    this.#logger = options.logger
    this.#newId = options.newId ?? defaultNewId
    this.#connectionState = options.connection.state
    this.#snapshot = this.#buildSnapshot()

    this.#subscriptions = [
      options.gateway.onEvent((event) => {
        this.#handleEvent(event)
      }),
      options.connection.onStateChange((state) => {
        this.#handleConnectionState(state)
      }),
    ]
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  /** Identity-stable between emissions, as `useSyncExternalStore` requires. */
  getSnapshot = (): ChatSessionSnapshot => this.#snapshot

  // FR-MSG-013 / FR-MSG-025 / FR-MSG-009: trim, refuse an empty or concurrent send without a
  // network call, then echo optimistically before the server has said anything.
  send(text: string): SendOutcome {
    const trimmed = text.trim()
    if (trimmed === '') {
      return 'empty'
    }

    if (this.#snapshot.awaitingResponse || !this.#snapshot.canSend) {
      return 'refused'
    }

    const turn = createTurn(turnId(this.#newId()), this.#message('user', trimmed))
    this.#turns = [...this.#turns, turn]

    // ADR-004: a dropped send is never queued. The echo stays, marked abandoned, and the caller
    // surfaces the notification.
    if (this.#gateway.sendChat(trimmed) === 'dropped-closed') {
      this.#resolve(turn.id, (pending) => abandonTurn(pending, 'never-sent'))
      return 'dropped-closed'
    }

    this.#emit()
    return 'sent'
  }

  dispose(): void {
    for (const unsubscribe of this.#subscriptions) {
      unsubscribe()
    }

    this.#subscriptions = []
    this.#listeners.clear()
  }

  #handleEvent(event: MessagingEvent): void {
    switch (event.kind) {
      case 'session-established':
        this.#sessionId = event.sessionId
        this.#emit()
        return
      case 'answered':
        this.#answer(event)
        return
      case 'failed':
        this.#fail(event.text)
        return
      case 'typing':
        this.#observeTyping(event.text)
        return
      case 'history-loaded':
        this.#history = event.messages.map((message, index) => ({
          message: createMessage(
            message.role,
            message.text,
            message.at,
            index + 1,
            message.id === null ? null : messageId(message.id),
          ),
          activity: message.activity,
        }))
        this.#seq = this.#history.length
        this.#turns = []
        this.#transient = []
        this.#configuredModel = null
        this.#emit()
    }
  }

  // FR-MSG-007. The optimistic user message is keyed by its client-generated turn id, so the
  // answer reconciles into that turn instead of appending a second copy of the request.
  #answer(event: Extract<MessagingEvent, { kind: 'answered' }>): void {
    this.#configuredModel = event.configuredModel
    const pending = this.#pendingTurn()
    const response = this.#message('assistant', event.text)

    // A duplicate/late answer for an already-resolved turn is still worth surfacing, as a transient.
    if (pending === undefined) {
      this.#pushTransient(response)
      return
    }

    this.#resolve(pending.id, (turn) =>
      answerTurn(
        turn,
        event.messageId === null ? response : { ...response, id: messageId(event.messageId) },
        event.conversationId === null ? turn.conversationId : conversationId(event.conversationId),
        {
          toolCalls: event.toolCalls,
          toolResults: event.toolResults,
          citations: event.citations,
          truncated: event.truncated,
        },
      ),
    )
  }

  // FR-MSG-007a: an `error` frame with no turn to fail is still worth showing, as a transient.
  #fail(text: string): void {
    const pending = this.#pendingTurn()

    if (pending === undefined) {
      this.#pushTransient(this.#message('system', text))
      return
    }

    this.#resolve(pending.id, (turn) => failTurn(turn, this.#message('assistant', text)))
  }

  // T-071 / FR-MSG-006 / ADR-008: a `typing` frame carries a cumulative snapshot, discriminated
  // between assistant text and tool-progress, and is applied to whichever turn is unresolved.
  #observeTyping(text: string): void {
    if (!this.#typingObserved) {
      this.#typingObserved = true
      this.#logger.info('ws_typing_observed', { length: text.length })
    }

    const pending = this.#pendingTurn()
    if (pending === undefined) {
      return
    }

    this.#resolve(pending.id, (turn) =>
      applyStreamingSnapshot(turn, text, classifyTypingFrame(text)),
    )
  }

  // FR-MSG-024: a drop or an intentional close leaves nothing awaiting a response forever.
  #handleConnectionState(state: ConnectionState): void {
    this.#connectionState = state

    if (state.status === 'open') {
      this.#emit()
      return
    }

    this.#sessionId = null
    const pending = this.#pendingTurn()

    if (pending === undefined) {
      this.#emit()
      return
    }

    this.#resolve(pending.id, abandonTurn)
  }

  #pendingTurn(): Turn | undefined {
    return this.#turns.find(isUnresolved)
  }

  #resolve(id: Turn['id'], transition: (turn: Turn) => TurnResult): void {
    this.#turns = this.#turns.map((turn) => {
      if (turn.id !== id) {
        return turn
      }

      const result = transition(turn)
      if (isOk(result)) {
        return result.value
      }

      this.#logger.warn('turn_transition_rejected', { ...result.error })
      return turn
    })

    this.#emit()
  }

  #pushTransient(message: ChatMessage): void {
    this.#transient = [...this.#transient, { id: `transient:${message.seq}`, message }]
    this.#emit()
  }

  #message(role: MessageRole, raw: string): ChatMessage {
    this.#seq += 1
    return createMessage(role, raw, this.#clock.now(), this.#seq)
  }

  #buildSnapshot(): ChatSessionSnapshot {
    const open = this.#connectionState.status === 'open'

    return {
      connection: connectionView(this.#connectionState.status),
      // I1: a session id only exists while the connection it was issued for is open.
      sessionId: open ? this.#sessionId : null,
      transcript: renderTranscript(this.#turns, this.#transient, this.#history),
      awaitingResponse: this.#pendingTurn() !== undefined,
      canSend: open,
      configuredModel: this.#configuredModel,
    }
  }

  #emit(): void {
    this.#snapshot = this.#buildSnapshot()

    for (const listener of this.#listeners) {
      listener()
    }
  }
}
