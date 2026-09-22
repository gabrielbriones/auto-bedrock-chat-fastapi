import type { ConversationId } from '@/shared/kernel/branded'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import { isOk } from '@/shared/kernel/result'
import type { ConnectionState, Unsubscribe } from '@/shared/ws/socket-client'

import type { ConversationEvent } from '@/domains/conversation/domain/events'
import { isPendingTurn } from '@/domains/conversation/domain/pending-turn'
import { reconcileRoster } from '@/domains/conversation/domain/reconcile-roster'
import {
  awaitFirstTurnId,
  beginBulkDelete,
  clearActive,
  clearSelection,
  emptyRoster,
  findConversation,
  selectAll,
  selectionState,
  toggleSelection,
  type ConversationRoster,
} from '@/domains/conversation/domain/roster'
import { PendingTurnWatcher } from '@/domains/conversation/application/pending-turn.watcher'
import {
  askForNewTitle,
  confirmBulkDelete,
  confirmDelete,
} from '@/domains/conversation/application/conversation-prompts'
import {
  reportConversationError,
  reportPartialDelete,
} from '@/domains/conversation/application/conversation-reporting'
import type {
  ConversationSnapshot,
  ConversationStoreOptions,
} from '@/domains/conversation/application/conversation-state'

export type {
  ConversationSnapshot,
  ConversationStoreOptions,
} from '@/domains/conversation/application/conversation-state'

// Frames that removed items; FR-CONV-016 refills the single page they left short.
const REFILLING: readonly ConversationEvent['kind'][] = ['deleted', 'bulk-deleted', 'all-deleted']

// SPEC-011 §4. Owns the roster read model and every conversation use case. All state changes go
// through `reconcileRoster`; nothing here patches the aggregate directly.
export class ConversationStore {
  readonly #options: ConversationStoreOptions
  readonly #listeners = new Set<() => void>()
  readonly #pendingTurn: PendingTurnWatcher
  #roster: ConversationRoster = emptyRoster
  #authenticated = false
  #persistenceDisabled = false
  #connected: boolean
  #notice: string | null = null
  #requestedId: ConversationId | null | undefined
  #unknownId: ConversationId | null = null
  #snapshot: ConversationSnapshot
  #subscriptions: readonly Unsubscribe[] = []

  constructor(options: ConversationStoreOptions) {
    this.#options = options
    this.#connected = options.connection.state.status === 'open'
    this.#pendingTurn = new PendingTurnWatcher({
      scheduler: options.scheduler,
      poll: (id) => {
        this.#requestLoad(id)
      },
      onChange: () => {
        this.#emit()
      },
    })
    this.#snapshot = this.#build()

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
  getSnapshot = (): ConversationSnapshot => this.#snapshot

  // FR-CONV-001: the composition root owns the identity dependency, so `conv` never imports `iam`.
  setAuthenticated(authenticated: boolean): void {
    if (this.#authenticated === authenticated) {
      return
    }

    const wasVisible = this.#visible()
    this.#authenticated = authenticated

    if (this.#visible() && !wasVisible) {
      this.#onBecameVisible()
      return
    }

    if (!this.#visible() && wasVisible) {
      this.#roster = clearActive(this.#roster)
      this.#requestedId = null
      this.#pendingTurn.stop()
    }

    this.#emit()
  }

  // FR-CONV-002 / P3: no reply frame is expected — the id arrives later.
  startNew(): void {
    if (!this.#guard(CONVERSATION_COPY.offline.create)) {
      return
    }

    this.#options.gateway.create()
    this.#roster = awaitFirstTurnId(this.#roster)
    this.#requestedId = null
    this.#pendingTurn.stop()
    this.#notice = null
    this.#unknownId = null
    this.#emit()
  }

  // FR-CONV-003. Opening a different conversation abandons any pending-turn watch on the old one
  // (FR-CONV-009b) before the reply that would otherwise extend it can arrive.
  open(id: ConversationId): void {
    if (!this.#guard(CONVERSATION_COPY.offline.load)) {
      return
    }

    if (this.#roster.activeId !== id) {
      this.#pendingTurn.stop()
    }

    this.#unknownId = null
    this.#requestLoad(id)
  }

  acceptsLoadedConversation(id: ConversationId): boolean {
    return this.#requestedId === undefined || this.#requestedId === id
  }

  // FR-CONV-009c: resumes from zero, because the user asked rather than because a frame arrived.
  retryPendingTurn(): void {
    if (this.#guard(CONVERSATION_COPY.offline.load)) {
      this.#pendingTurn.retry()
    }
  }

  /** FR-MSG-008: leaves the recovery state without loading anything. */
  dismissUnknownId(): void {
    this.#unknownId = null
    this.#emit()
  }

  refresh(): void {
    if (!this.#guard(CONVERSATION_COPY.offline.refresh)) {
      return
    }

    this.#options.gateway.requestRoster()
  }

  // FR-CONV-004 / FIX-15: the shared prompt, never `window.prompt`. Cancelling resolves null and
  // makes no request; the dialog itself refuses to submit a blank title.
  async rename(id: ConversationId): Promise<void> {
    const conversation = findConversation(this.#roster, id)

    if (conversation === undefined) {
      return
    }

    const title = await askForNewTitle(this.#options.confirmations, conversation.title)

    if (title === null) {
      return
    }

    if (title === 'empty') {
      this.#options.notifications.error(CONVERSATION_COPY.rename.empty)
      return
    }

    if (this.#guard(CONVERSATION_COPY.offline.rename)) {
      this.#options.gateway.rename(id, title)
    }
  }

  // FR-CONV-005 / FIX-15: the confirmation names the conversation, never `window.confirm`.
  async remove(id: ConversationId): Promise<void> {
    const conversation = findConversation(this.#roster, id)

    if (conversation === undefined) {
      return
    }

    const confirmed = await confirmDelete(this.#options.confirmations, conversation.title)

    if (confirmed && this.#guard(CONVERSATION_COPY.offline.delete)) {
      this.#options.gateway.remove(id)
    }
  }

  // FR-CONV-006: the guard is checked before the confirmation is even offered, so a second
  // activation cannot queue behind the first and send a duplicate frame when both resolve.
  async removeSelected(): Promise<void> {
    const ids = [...this.#roster.selection]

    if (ids.length === 0) {
      return
    }

    if (this.#roster.pendingBulkDelete !== null) {
      this.#options.notifications.warning(CONVERSATION_COPY.bulk.inFlight)
      return
    }

    if (await confirmBulkDelete(this.#options.confirmations, ids.length)) {
      this.#startBulkDelete(ids)
    }
  }

  toggleSelected(id: ConversationId): void {
    this.#roster = toggleSelection(this.#roster, id)
    this.#emit()
  }

  // FR-CONV-006: the select-all control drives both directions from one indeterminate checkbox.
  toggleSelectAll(): void {
    this.#roster =
      selectionState(this.#roster) === 'all' ? clearSelection(this.#roster) : selectAll(this.#roster)
    this.#emit()
  }

  clearSelected(): void {
    this.#roster = clearSelection(this.#roster)
    this.#emit()
  }

  dispose(): void {
    for (const unsubscribe of this.#subscriptions) {
      unsubscribe()
    }

    this.#pendingTurn.dispose()
    this.#subscriptions = []
    this.#listeners.clear()
  }

  #startBulkDelete(ids: readonly ConversationId[]): void {
    if (!this.#guard(CONVERSATION_COPY.offline.delete)) {
      return
    }

    const guarded = beginBulkDelete(this.#roster, ids)

    if (!isOk(guarded)) {
      this.#options.notifications.warning(CONVERSATION_COPY.bulk.inFlight)
      return
    }

    this.#roster = guarded.value
    this.#options.gateway.removeMany(ids)
    this.#emit()
  }

  #handleEvent(event: ConversationEvent): void {
    if (event.kind === 'loaded' && !this.acceptsLoadedConversation(event.id)) {
      return
    }

    if (event.kind === 'created' && this.#roster.awaitingIdForFirstTurn) {
      this.#requestedId = event.id
    }

    if (event.kind === 'error') {
      this.#applyError(event)
    }

    if (event.kind === 'bulk-deleted') {
      reportPartialDelete(
        this.#roster.pendingBulkDelete,
        event.deletedIds,
        this.#options.notifications,
        this.#options.logger,
      )
    }

    // FR-CONV-009 / P8: whether the server is still working is decided from the history it just
    // returned, on every load — including the ones this watch itself issued.
    if (event.kind === 'loaded') {
      this.#unknownId = null
      this.#pendingTurn.observe(event.id, isPendingTurn(event.messages))
    }

    this.#roster = reconcileRoster(this.#roster, event, this.#options.clock.now())

    // FR-CONV-016: refetch rather than trusting the in-place mutation, so the list and its total
    // come from the server (FIX-16).
    if (REFILLING.includes(event.kind) && this.#visible() && this.#connected) {
      this.#options.gateway.requestRoster()
    }

    if (this.#roster.activeId === null) {
      this.#pendingTurn.stop()
    }

    this.#emit()
  }

  #applyError(event: Extract<ConversationEvent, { kind: 'error' }>): void {
    // FR-MSG-008 / T-096: an id the server has never heard of has a recovery state of its own, not
    // an error boundary and not a blank transcript.
    if (event.code === 'conversation_not_found') {
      this.#unknownId = event.id
      this.#pendingTurn.stop()
    }

    const outcome = reportConversationError(event, this.#options.notifications, this.#options.logger)
    this.#notice = outcome.notice
    this.#persistenceDisabled = this.#persistenceDisabled || outcome.disablePersistence

    if (outcome.refresh) {
      this.#refreshIfVisible()
    }
  }

  // FR-CONV-012 / P2 / P4: the server session is stateless across sockets, so a reconnect re-asks
  // for everything rather than assuming the roster it already rendered is still current.
  #handleConnectionState(state: ConnectionState): void {
    const connected = state.status === 'open'
    const reconnected = connected && !this.#connected
    this.#connected = connected

    // FR-CONV-009b: a closed socket cannot answer a poll, so the watch stops rather than burning
    // its remaining attempts against nothing.
    if (!connected) {
      this.#pendingTurn.stop()
    }

    if (reconnected) {
      this.#refreshIfVisible()
    }

    this.#emit()
  }

  #onBecameVisible(): void {
    this.#refreshIfVisible()
    this.#emit()
  }

  #refreshIfVisible(): void {
    if (!this.#visible() || !this.#connected) {
      return
    }

    this.#options.gateway.requestRoster()

    // P4: an active conversation has to be reloaded explicitly — the new socket has no memory of it.
    if (this.#roster.activeId !== null) {
      this.#requestLoad(this.#roster.activeId)
    }
  }

  #requestLoad(id: ConversationId): void {
    this.#requestedId = id
    this.#options.gateway.load(id)
  }

  // FR-CONV-001.
  #visible(): boolean {
    return this.#options.persistenceEnabled && this.#authenticated && !this.#persistenceDisabled
  }

  // FR-CONV-017 / ADR-012: no optimistic mutation while the socket is closed, and the user is told
  // which mutation did not happen rather than being left to notice nothing changed.
  #guard(offlineMessage: string): boolean {
    if (this.#connected) {
      return true
    }

    this.#options.notifications.error(offlineMessage)
    return false
  }

  #build(): ConversationSnapshot {
    return {
      visible: this.#visible(),
      items: this.#roster.items,
      activeId: this.#roster.activeId,
      selection: this.#roster.selection,
      selectionState: selectionState(this.#roster),
      // eslint-disable-next-line max-lines
      bulkDeleteInFlight: this.#roster.pendingBulkDelete !== null,
      connected: this.#connected,
      notice: this.#notice,
      pendingTurn: this.#pendingTurn.watch,
      unknownId: this.#unknownId,
      awaitingId: this.#roster.awaitingIdForFirstTurn,
    }
  }

  #emit(): void {
    this.#snapshot = this.#build()

    for (const listener of this.#listeners) {
      listener()
    }
  }
}
