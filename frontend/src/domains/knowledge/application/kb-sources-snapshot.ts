import type { Problem } from '@/shared/http/exception'
import type { ConfirmationPort } from '@/shared/ports/confirmation-port'
import type { Logger } from '@/shared/logging/logger'
import type { NotificationPort } from '@/shared/ports/notification-port'

import type { KbSourcesGateway, PollScheduler } from '@/domains/knowledge/application/ports'
import { IDLE_RUN, type KbSourceRun, type KbSourceSummary, type KbSourceType } from '@/domains/knowledge/domain/public'

export type KbSourcesListStatus = 'idle' | 'loading' | 'ready' | 'error'

export type KbSourcesSnapshot = {
  readonly sources: readonly KbSourceSummary[]
  readonly sourcesStatus: KbSourcesListStatus
  readonly sourcesProblem: Problem | null
  /** The most recent run the server reported; `IDLE_RUN` until the first status read. */
  readonly run: KbSourceRun
  readonly runProblem: Problem | null
  /** Which form's request is in flight; both forms are disabled while either is. */
  readonly submitting: KbSourceType | null
  /** The source whose delete is in flight. */
  readonly deleting: string | null
}

export type KbSourcesStoreOptions = {
  readonly gateway: KbSourcesGateway
  readonly confirmations: ConfirmationPort
  readonly notifications: NotificationPort
  readonly scheduler: PollScheduler
  readonly logger: Logger
  /** Legacy dashboard polled every 1.5 s. */
  readonly pollIntervalMs?: number
}

export const DEFAULT_POLL_INTERVAL_MS = 1_500

export const createInitialKbSourcesSnapshot = (): KbSourcesSnapshot => ({
  sources: [],
  sourcesStatus: 'idle',
  sourcesProblem: null,
  run: IDLE_RUN,
  runProblem: null,
  submitting: null,
  deleting: null,
})
