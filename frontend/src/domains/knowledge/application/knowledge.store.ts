import { isErr } from '@/shared/kernel/result'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import type { KbDocumentId } from '@/shared/kernel/branded'

import type { KbQuery } from '@/domains/knowledge/application/ports'
import type { KbDocument, SparsePatch } from '@/domains/knowledge/domain/public'
import { toSummary } from '@/domains/knowledge/domain/public'
import {
  createInitialKnowledgeSnapshot,
  emptyKnowledgePage,
  type KnowledgeSnapshot,
  type KnowledgeStoreOptions,
} from '@/domains/knowledge/application/knowledge-snapshot'
import {
  previousOffsetAfterDelete,
  withDetailReset,
} from '@/domains/knowledge/application/knowledge-store-helpers'

export type {
  KnowledgeListStatus,
  KnowledgeSnapshot,
  KnowledgeStoreOptions,
} from '@/domains/knowledge/application/knowledge-snapshot'

export class KnowledgeStore {
  readonly #options: KnowledgeStoreOptions
  readonly #listeners = new Set<() => void>()
  #listController: AbortController | null = null
  #listSequence = 0
  #detailController: AbortController | null = null
  #detailSequence = 0
  #currentQuery: KbQuery | null = null
  #snapshot: KnowledgeSnapshot = createInitialKnowledgeSnapshot()

  constructor(options: KnowledgeStoreOptions) {
    this.#options = options
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    return () => {
      this.#listeners.delete(listener)
    }
  }

  getSnapshot = (): KnowledgeSnapshot => this.#snapshot

  async load(query: KbQuery): Promise<void> {
    this.#currentQuery = query
    this.#listController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#listSequence
    this.#listController = controller
    this.#snapshot = {
      ...this.#snapshot,
      page: emptyKnowledgePage(query.limit, query.offset),
      listStatus: 'loading',
      listProblem: null,
    }
    this.#emit()

    const result = await this.#options.gateway.list(query, controller.signal)

    if (sequence !== this.#listSequence || controller.signal.aborted) {
      return
    }

    this.#snapshot = isErr(result)
      ? { ...this.#snapshot, listStatus: 'error', listProblem: result.error }
      : { ...this.#snapshot, page: result.value, listStatus: 'ready', listProblem: null }
    this.#emit()
  }

  async open(id: KbDocumentId): Promise<void> {
    this.#detailController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#detailSequence
    this.#detailController = controller
    this.#snapshot = withDetailReset(this.#snapshot, 'loading')
    this.#emit()

    const result = await this.#options.gateway.get(id, controller.signal)

    if (sequence !== this.#detailSequence || controller.signal.aborted) {
      return
    }

    this.#snapshot = isErr(result)
      ? { ...this.#snapshot, detailStatus: 'error', detailProblem: result.error }
      : { ...this.#snapshot, activeDocument: result.value, detailStatus: 'ready', detailProblem: null }
    this.#emit()
  }

  close(): void {
    this.#detailController?.abort()
    this.#detailSequence += 1
    this.#snapshot = withDetailReset(this.#snapshot, 'idle')
    this.#emit()
  }

  // FR-KB-021/FIX-04: an empty sparse patch means nothing actually changed, so it is a no-op
  // rather than a round trip. A `content` field always triggers a live re-embed on the server, so
  // it is warned about — and cancellable — before the request goes out.
  async save(id: KbDocumentId, patch: SparsePatch): Promise<boolean> {
    if (Object.keys(patch).length === 0) {
      return true
    }

    if (this.#snapshot.mutationPending) {
      return false
    }

    if ('content' in patch) {
      const proceed = await this.#options.confirmations.confirm({
        title: KNOWLEDGE_COPY.editor.reembedWarningTitle,
        message: KNOWLEDGE_COPY.editor.reembedWarningMessage,
        confirmLabel: KNOWLEDGE_COPY.editor.reembedWarningConfirm,
        tone: 'destructive',
      })

      if (!proceed) {
        return false
      }
    }

    this.#setMutation(true)
    const result = await this.#options.gateway.patch(id, patch)

    if (isErr(result)) {
      if (result.error.status !== 409 && result.error.status !== 422) {
        this.#options.notifications.error(KNOWLEDGE_COPY.editor.saveFailure)
      }
      this.#snapshot = { ...this.#snapshot, mutationPending: false, saveProblem: result.error }
      this.#emit()
      return false
    }

    this.#options.notifications.success(KNOWLEDGE_COPY.editor.saveSuccess)
    this.#applyDocument(result.value)
    this.#snapshot = { ...this.#snapshot, saveProblem: null }
    this.#setMutation(false)
    return true
  }

  // FR-KB-023: updates the credibility in place — no full list/detail reload.
  async resetCredibility(id: KbDocumentId): Promise<boolean> {
    if (this.#snapshot.mutationPending) {
      return false
    }

    this.#setMutation(true)
    const result = await this.#options.gateway.resetCredibility(id)

    if (isErr(result)) {
      this.#options.notifications.error(KNOWLEDGE_COPY.editor.resetCredibilityFailure)
      this.#setMutation(false)
      return false
    }

    this.#options.notifications.success(KNOWLEDGE_COPY.editor.resetCredibilitySuccess)
    this.#applyDocument(result.value)
    this.#setMutation(false)
    return true
  }

  async rollback(id: KbDocumentId): Promise<boolean> {
    if (this.#snapshot.mutationPending) {
      return false
    }

    const confirmed = await this.#options.confirmations.confirm({
      title: KNOWLEDGE_COPY.editor.rollbackConfirmTitle,
      message: KNOWLEDGE_COPY.editor.rollbackConfirmMessage,
      confirmLabel: KNOWLEDGE_COPY.editor.rollbackConfirmLabel,
      tone: 'destructive',
    })

    if (!confirmed) {
      return false
    }

    this.#setMutation(true)
    const result = await this.#options.gateway.rollback(id, null)

    if (isErr(result)) {
      this.#options.notifications.error(KNOWLEDGE_COPY.editor.rollbackFailure)
      this.#setMutation(false)
      return false
    }

    this.#options.notifications.success(KNOWLEDGE_COPY.editor.rollbackSuccess)
    this.close()
    await this.#reload()
    this.#setMutation(false)
    return true
  }

  // Returns the offset the caller should navigate back to when the deleted row was the page's
  // last, or null when the current offset is still valid; null also means "did not delete".
  async remove(id: KbDocumentId): Promise<number | null> {
    if (this.#snapshot.mutationPending) {
      return null
    }

    const confirmed = await this.#options.confirmations.confirm({
      title: KNOWLEDGE_COPY.editor.deleteConfirmTitle,
      message: KNOWLEDGE_COPY.editor.deleteConfirmMessage,
      confirmLabel: KNOWLEDGE_COPY.editor.deleteConfirmLabel,
      tone: 'destructive',
    })

    if (!confirmed) {
      return null
    }

    this.#setMutation(true)
    const result = await this.#options.gateway.remove(id)

    if (isErr(result)) {
      this.#options.notifications.error(KNOWLEDGE_COPY.editor.deleteFailure)
      this.#setMutation(false)
      return null
    }

    this.#options.notifications.success(KNOWLEDGE_COPY.editor.deleteSuccess)
    const previousOffset = previousOffsetAfterDelete(this.#snapshot.page)
    this.close()
    await this.#reload()
    this.#setMutation(false)
    return previousOffset
  }

  dispose(): void {
    this.#listController?.abort()
    this.#detailController?.abort()
    this.#listeners.clear()
  }

  #applyDocument(document: KbDocument): void {
    const page = this.#snapshot.page
    const items = page.items.map((item) => (item.id === document.id ? toSummary(document) : item))
    this.#snapshot = { ...this.#snapshot, activeDocument: document, page: { ...page, items } }
  }

  #setMutation(pending: boolean): void {
    this.#snapshot = { ...this.#snapshot, mutationPending: pending }
    this.#emit()
  }

  async #reload(): Promise<void> {
    if (this.#currentQuery !== null) {
      await this.load(this.#currentQuery)
    }
  }

  #emit(): void {
    for (const listener of this.#listeners) {
      listener()
    }
  }
}