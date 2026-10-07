import { isErr, type Result } from '@/shared/kernel/result'
import type { Problem } from '@/shared/http/exception'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'

import type { Cancel } from '@/domains/knowledge/application/ports'
import {
  isRunActive,
  isRunTerminal,
  type FileIngestRequest,
  type KbSourceRun,
  type KbSourceSummary,
  type KbSourceFilter,
  type KbSourceType,
  type WebCrawlRequest,
} from '@/domains/knowledge/domain/public'
import {
  createInitialKbSourcesSnapshot,
  DEFAULT_POLL_INTERVAL_MS,
  type KbSourcesSnapshot,
  type KbSourcesStoreOptions,
} from '@/domains/knowledge/application/kb-sources-snapshot'

export type {
  KbSourcesListStatus,
  KbSourcesSnapshot,
  KbSourcesStoreOptions,
} from '@/domains/knowledge/application/kb-sources-snapshot'

const SOURCES = KNOWLEDGE_COPY.sources

const isDuplicateSource = (problem: Problem): boolean =>
  problem.status === 409 && problem.serverCode === 'source_already_exists'

const isRunInProgress = (problem: Problem): boolean => problem.status === 409 && !isDuplicateSource(problem)

type RunCopy = {
  readonly started: (runId: string) => string
  readonly overrideStarted: (runId: string) => string
  readonly failure: string
}

export class KbSourcesStore {
  readonly #options: KbSourcesStoreOptions
  readonly #listeners = new Set<() => void>()
  #sourcesController: AbortController | null = null
  #sourcesSequence = 0
  #statusController: AbortController | null = null
  #statusSequence = 0
  #cancelPoll: Cancel | null = null
  #polling = false
  #snapshot: KbSourcesSnapshot = createInitialKbSourcesSnapshot()

  constructor(options: KbSourcesStoreOptions) {
    this.#options = options
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    return () => {
      this.#listeners.delete(listener)
    }
  }

  getSnapshot = (): KbSourcesSnapshot => this.#snapshot

  // Page mount: the list and the run status load together, and a run already in progress (the
  // admin reloaded mid-crawl) is picked up and polled so the panel reflects reality immediately.
  async load(): Promise<void> {
    await Promise.all([this.loadSources(), this.refreshStatus()])
  }

  async loadSources(): Promise<void> {
    this.#sourcesController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#sourcesSequence
    this.#sourcesController = controller
    this.#patch({ sourcesStatus: 'loading', sourcesProblem: null })

    const result = await this.#options.gateway.listSources(
      { ...this.#snapshot.sourcePage, sourceType: this.#snapshot.sourceType }, controller.signal,
    )
    if (sequence !== this.#sourcesSequence || controller.signal.aborted) {
      return
    }

    if (isErr(result)) {
      this.#patch({ sourcesStatus: 'error', sourcesProblem: result.error })
      return
    }

    const { items, total, limit, offset } = result.value
    if (items.length === 0 && offset > 0 && total <= offset) {
      // A delete/override may shrink the final page: navigate to the last nonempty page.
      this.#patch({ sourcePage: { limit, offset: Math.max(0, Math.ceil(total / limit) - 1) * limit } })
      await this.loadSources()
      return
    }
    this.#patch({ sources: items, sourceTotal: total, sourcePage: { limit, offset }, sourcesStatus: 'ready', sourcesProblem: null })
  }

  async setSourceOffset(offset: number): Promise<void> {
    if (offset === this.#snapshot.sourcePage.offset) return
    this.#patch({ sourcePage: { ...this.#snapshot.sourcePage, offset } })
    await this.loadSources()
  }

  async setSourceType(sourceType: KbSourceFilter): Promise<void> {
    if (sourceType === this.#snapshot.sourceType) return
    this.#patch({ sourceType, sourcePage: { ...this.#snapshot.sourcePage, offset: 0 } })
    await this.loadSources()
  }

  // Route-driven: the URL is the authority, so this always reloads, even on the initial defaults.
  async setSourceQuery(query: { readonly sourceType: KbSourceFilter; readonly offset: number }): Promise<void> {
    this.#patch({ sourceType: query.sourceType, sourcePage: { ...this.#snapshot.sourcePage, offset: query.offset } })
    await this.loadSources()
  }

  async refreshStatus(): Promise<void> {
    this.#statusController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#statusSequence
    this.#statusController = controller

    const result = await this.#options.gateway.status(controller.signal)
    if (sequence !== this.#statusSequence || controller.signal.aborted) {
      return
    }

    if (isErr(result)) {
      this.#stopPolling()
      this.#patch({ runProblem: result.error })
      return
    }

    this.#patch({ run: result.value, runProblem: null })
    this.#followRun(result.value)
  }

  startWebCrawl(request: WebCrawlRequest): Promise<boolean> {
    return this.#start('web', request.name, SOURCES.web, {
      start: () => this.#options.gateway.startWebCrawl(request),
      override: () => this.#options.gateway.overrideWebCrawl(request),
    })
  }

  startFileIngest(request: FileIngestRequest): Promise<boolean> {
    return this.#start('file', request.name, SOURCES.file, {
      start: () => this.#options.gateway.startFileIngest(request),
      override: () => this.#options.gateway.overrideFileIngest(request),
    })
  }

  async deleteSource(summary: KbSourceSummary): Promise<boolean> {
    if (this.#snapshot.deleting !== null) {
      return false
    }

    const confirmed = await this.#options.confirmations.confirm({
      title: SOURCES.list.deleteConfirmTitle,
      message: SOURCES.list.deleteConfirmMessage(summary.source, summary.documentCount),
      confirmLabel: SOURCES.list.deleteConfirmLabel,
      tone: 'destructive',
    })
    if (!confirmed) {
      return false
    }

    this.#patch({ deleting: summary.source })
    const result = await this.#options.gateway.deleteSource(summary.source)
    this.#patch({ deleting: null })

    if (isErr(result)) {
      this.#options.notifications.error(SOURCES.list.deleteFailure, this.#detailOf(result.error))
      return false
    }

    this.#options.notifications.success(
      SOURCES.list.deleteSuccess(summary.source, result.value.deleted ?? summary.documentCount),
    )
    await this.loadSources()
    return true
  }

  dispose(): void {
    this.#sourcesController?.abort()
    this.#statusController?.abort()
    this.#stopPolling()
    this.#listeners.clear()
  }

  // POST first; a `409 source_already_exists` becomes a confirm-then-PUT so re-running a source
  // never silently creates a second set of documents (admin-api.md "Duplicate source names").
  async #start(
    type: KbSourceType,
    name: string,
    copy: RunCopy,
    calls: { readonly start: () => Promise<Result<KbSourceRun, Problem>>; readonly override: () => Promise<Result<KbSourceRun, Problem>> },
  ): Promise<boolean> {
    if (this.#snapshot.submitting !== null) {
      return false
    }

    this.#patch({ submitting: type })
    let result = await calls.start()
    let overridden = false

    if (isErr(result) && isDuplicateSource(result.error)) {
      const proceed = await this.#options.confirmations.confirm({
        title: SOURCES.override.title,
        message: SOURCES.override.message(name),
        confirmLabel: SOURCES.override.confirm,
        tone: 'destructive',
      })
      if (!proceed) {
        this.#patch({ submitting: null })
        return false
      }
      result = await calls.override()
      overridden = true
    }

    this.#patch({ submitting: null })

    if (isErr(result)) {
      this.#reportStartFailure(result.error, copy)
      return false
    }

    const runId = result.value.runId ?? SOURCES.run.noRunId
    this.#options.notifications.success(overridden ? copy.overrideStarted(runId) : copy.started(runId))
    this.#patch({ run: result.value, runProblem: null })
    this.#followRun(result.value)
    return true
  }

  #reportStartFailure(problem: Problem, copy: RunCopy): void {
    if (isRunInProgress(problem)) {
      // Another run holds the single global lock: show it rather than an error.
      this.#options.notifications.info(SOURCES.run.inProgress)
      this.#startPolling()
      return
    }

    this.#options.notifications.error(copy.failure, this.#detailOf(problem))
  }

  #detailOf(problem: Problem): { readonly description: string } | undefined {
    return problem.detail === undefined ? undefined : { description: problem.detail }
  }

  #followRun(run: KbSourceRun): void {
    if (isRunActive(run)) {
      this.#startPolling()
      return
    }

    if (this.#polling) {
      this.#stopPolling()
      if (isRunTerminal(run)) {
        this.#announceOutcome(run)
        void this.loadSources()
      }
    }
  }

  #announceOutcome(run: KbSourceRun): void {
    if (run.phase === 'failed') {
      this.#options.notifications.error(SOURCES.run.failed(run.error ?? SOURCES.run.unknownError))
      return
    }

    if (run.errors.length > 0) {
      this.#options.notifications.info(SOURCES.run.finishedWithErrors(run.chunksWritten, run.errors.length))
      return
    }

    this.#options.notifications.success(SOURCES.run.finished(run.chunksWritten))
  }

  // Safe to call repeatedly: a second call while already polling is a no-op.
  #startPolling(): void {
    if (this.#polling) {
      return
    }

    this.#polling = true
    this.#scheduleNextPoll()
  }

  #scheduleNextPoll(): void {
    this.#cancelPoll?.()
    this.#cancelPoll = this.#options.scheduler.schedule(() => {
      this.#cancelPoll = null
      void this.refreshStatus().then(() => {
        if (this.#polling) {
          this.#scheduleNextPoll()
        }
      })
    }, this.#options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS)
  }

  #stopPolling(): void {
    this.#polling = false
    this.#cancelPoll?.()
    this.#cancelPoll = null
  }

  #patch(change: Partial<KbSourcesSnapshot>): void {
    this.#snapshot = { ...this.#snapshot, ...change }
    for (const listener of this.#listeners) {
      listener()
    }
  }
}
