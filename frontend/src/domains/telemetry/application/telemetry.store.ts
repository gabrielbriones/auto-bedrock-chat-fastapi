import { isErr } from '@/shared/kernel/result'

import type { ByUserUsageQuery } from '@/domains/telemetry/application/ports'
import {
  createCursorlessPage,
  type DateRange,
} from '@/domains/telemetry/domain/public'
import {
  createInitialTelemetrySnapshot,
  type TelemetrySnapshot,
  type TelemetryStoreOptions,
} from '@/domains/telemetry/application/telemetry-snapshot'

export type {
  TelemetryListStatus,
  TelemetrySection,
  TelemetrySnapshot,
  TelemetryStoreOptions,
} from '@/domains/telemetry/application/telemetry-snapshot'

export class TelemetryStore {
  readonly #options: TelemetryStoreOptions
  readonly #listeners = new Set<() => void>()
  #summaryController: AbortController | null = null
  #summarySequence = 0
  #topUsersController: AbortController | null = null
  #topUsersSequence = 0
  #byDayController: AbortController | null = null
  #byDaySequence = 0
  #byDayRejectedKey: string | null = null
  #byUserController: AbortController | null = null
  #byUserSequence = 0
  #snapshot: TelemetrySnapshot

  constructor(options: TelemetryStoreOptions) {
    this.#options = options
    this.#snapshot = createInitialTelemetrySnapshot()
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    return () => this.#listeners.delete(listener)
  }

  getSnapshot = (): TelemetrySnapshot => this.#snapshot

  async loadSummary(): Promise<void> {
    this.#summaryController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#summarySequence
    this.#summaryController = controller
    this.#snapshot = {
      ...this.#snapshot,
      summary: { ...this.#snapshot.summary, status: 'loading', problem: null },
    }
    this.#emit()

    const result = await this.#options.gateway.summary(controller.signal)
    if (!this.#isCurrent(sequence, controller.signal, this.#summarySequence)) return

    this.#snapshot = isErr(result)
      ? { ...this.#snapshot, summary: { ...this.#snapshot.summary, status: 'error', problem: result.error } }
      : { ...this.#snapshot, summary: { rows: result.value, status: 'ready', problem: null } }
    this.#emit()
  }

  async loadTopUsers(limit: number): Promise<void> {
    this.#topUsersController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#topUsersSequence
    this.#topUsersController = controller
    this.#snapshot = {
      ...this.#snapshot,
      topUsers: { ...this.#snapshot.topUsers, limit, status: 'loading', problem: null },
    }
    this.#emit()

    const result = await this.#options.gateway.topUsers(limit, controller.signal)
    if (!this.#isCurrent(sequence, controller.signal, this.#topUsersSequence)) return

    this.#snapshot = isErr(result)
      ? { ...this.#snapshot, topUsers: { ...this.#snapshot.topUsers, status: 'error', problem: result.error } }
      : { ...this.#snapshot, topUsers: { rows: result.value, limit, status: 'ready', problem: null } }
    this.#emit()
  }

  async loadByDay(range: DateRange): Promise<void> {
    const key = dateRangeKey(range)
    if (this.#byDayRejectedKey === key) return

    this.#byDayController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#byDaySequence
    this.#byDayController = controller
    this.#snapshot = {
      ...this.#snapshot,
      byDay: { ...this.#snapshot.byDay, range, status: 'loading', problem: null },
    }
    this.#emit()

    const result = await this.#options.gateway.byDay(range, controller.signal)
    if (!this.#isCurrent(sequence, controller.signal, this.#byDaySequence)) return

    if (isErr(result)) {
      const rejected = isInvalidDateRangeProblem(result.error)
      if (rejected) this.#byDayRejectedKey = key
      this.#snapshot = {
        ...this.#snapshot,
        byDay: {
          rows: [],
          range: rejected ? null : range,
          status: rejected ? 'idle' : 'error',
          problem: result.error,
        },
      }
    } else {
      this.#snapshot = {
        ...this.#snapshot,
        byDay: { rows: result.value, range, status: 'ready', problem: null },
      }
    }
    this.#emit()
  }

  async loadByUser(query: ByUserUsageQuery): Promise<void> {
    this.#byUserController?.abort()
    const controller = new AbortController()
    const sequence = ++this.#byUserSequence
    this.#byUserController = controller
    const pendingPage = createCursorlessPage(query.page.offset, query.page.limit, 0)
    this.#snapshot = {
      ...this.#snapshot,
      byUser: {
        ...this.#snapshot.byUser,
        userId: query.userId,
        page: pendingPage,
        status: 'loading',
        problem: null,
      },
    }
    this.#emit()

    const result = await this.#options.gateway.byUser(query, controller.signal)
    if (!this.#isCurrent(sequence, controller.signal, this.#byUserSequence)) return

    this.#snapshot = isErr(result)
      ? { ...this.#snapshot, byUser: { ...this.#snapshot.byUser, status: 'error', problem: result.error } }
      : {
          ...this.#snapshot,
          byUser: {
            rows: result.value,
            userId: query.userId,
            page: createCursorlessPage(query.page.offset, query.page.limit, result.value.length),
            status: 'ready',
            problem: null,
          },
        }
    this.#emit()
  }

  resetByDay(): void {
    this.#byDayController?.abort()
    this.#byDaySequence += 1
    this.#byDayRejectedKey = null
    this.#snapshot = {
      ...this.#snapshot,
      byDay: { rows: [], range: null, status: 'idle', problem: null },
    }
    this.#emit()
  }

  resetByUser(): void {
    this.#byUserController?.abort()
    this.#byUserSequence += 1
    this.#snapshot = {
      ...this.#snapshot,
      byUser: {
        rows: [],
        userId: '',
        page: createCursorlessPage(0, this.#snapshot.byUser.page.limit, 0),
        status: 'idle',
        problem: null,
      },
    }
    this.#emit()
  }

  dispose(): void {
    this.#summaryController?.abort()
    this.#topUsersController?.abort()
    this.#byDayController?.abort()
    this.#byUserController?.abort()
    this.#summarySequence += 1
    this.#topUsersSequence += 1
    this.#byDaySequence += 1
    this.#byUserSequence += 1
    this.#listeners.clear()
  }

  #isCurrent(sequence: number, signal: AbortSignal, current: number): boolean {
    return sequence === current && !signal.aborted
  }

  #emit(): void {
    for (const listener of this.#listeners) listener()
  }
}

const dateRangeKey = (range: DateRange): string => `${range.start.toIso()}/${range.end.toIso()}`

const isInvalidDateRangeProblem = (problem: { readonly status?: number; readonly serverCode?: string }): boolean =>
  problem.status === 400 && problem.serverCode === 'invalid_date_range'