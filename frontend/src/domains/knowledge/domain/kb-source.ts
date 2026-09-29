import type { Instant } from '@/shared/kernel/instant'

// A KB "source" is the `source` column shared by every document, however it was ingested (web
// crawl, file upload or the offline populate pipeline). The admin API lists distinct values with
// their document counts; there is no per-run history — completed runs leave only documents behind.
export type KbSourceSummary = {
  readonly source: string
  readonly count: number
}

export type KbSourcePhase = 'idle' | 'running' | 'completed' | 'failed'

export type KbSourceType = 'web' | 'file'

// The single global ingestion run. `POST`/`PUT` answer with it on claim and `GET .../status`
// reports the most recent one, resetting to `idle` on process restart.
export type KbSourceRun = {
  readonly runId: string | null
  readonly phase: KbSourcePhase
  readonly sourceName: string | null
  readonly sourceType: KbSourceType | null
  readonly startedAt: Instant | null
  readonly finishedAt: Instant | null
  readonly pagesCrawled: number
  readonly pagesProcessed: number
  readonly filesProcessed: number
  readonly chunksWritten: number
  /** Fatal error that aborted the whole run. */
  readonly error: string | null
  /** Per-item failures that did not abort the run; a completed run may still list many. */
  readonly errors: readonly string[]
}

export const IDLE_RUN: KbSourceRun = Object.freeze({
  runId: null,
  phase: 'idle',
  sourceName: null,
  sourceType: null,
  startedAt: null,
  finishedAt: null,
  pagesCrawled: 0,
  pagesProcessed: 0,
  filesProcessed: 0,
  chunksWritten: 0,
  error: null,
  errors: [],
})

export const isRunActive = (run: KbSourceRun): boolean => run.phase === 'running'

export const isRunTerminal = (run: KbSourceRun): boolean =>
  run.phase === 'completed' || run.phase === 'failed'

// A web run counts pages, a file run counts files; the panel shows whichever the run is about.
export const processedCount = (run: KbSourceRun): number =>
  run.sourceType === 'file' ? run.filesProcessed : run.pagesProcessed

export type WebCrawlRequest = {
  readonly name: string
  readonly urls: readonly string[]
  readonly topic: string | null
  readonly maxDepth: number | null
  readonly maxPages: number | null
  readonly allowedDomains: readonly string[] | null
  readonly excludePatterns: readonly string[] | null
  readonly ingestLinkedFiles: boolean
  readonly headers: Readonly<Record<string, string>> | null
  readonly cookies: Readonly<Record<string, string>> | null
}

export type FileIngestRequest = {
  readonly name: string
  readonly topic: string | null
  readonly files: readonly File[]
}
