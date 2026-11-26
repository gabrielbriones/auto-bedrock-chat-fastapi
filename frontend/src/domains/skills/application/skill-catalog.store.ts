import type { Clock } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

import type { SkillCatalogGateway } from '@/domains/skills/application/ports'
import type { SkillCatalogEntry } from '@/domains/skills/domain/public'

// Admins can enable a skill at any time, so a focused composer re-reads a catalog older than this.
export const SKILL_CATALOG_STALE_MS = 60_000

export type SkillCatalogSnapshot = {
  readonly entries: readonly SkillCatalogEntry[]
  readonly status: 'idle' | 'loading' | 'ready' | 'failed'
}

export type SkillCatalogStoreOptions = {
  readonly gateway: SkillCatalogGateway
  readonly clock: Clock
  readonly logger: Logger
}

// The slash-command menu's data. A failed read keeps the last entries: the server parses
// commands anyway, so the catalog only ever helps and never blocks sending.
export class SkillCatalogStore {
  readonly #gateway: SkillCatalogGateway
  readonly #clock: Clock
  readonly #logger: Logger
  readonly #listeners = new Set<() => void>()
  #snapshot: SkillCatalogSnapshot = { entries: [], status: 'idle' }
  #loadedAt: number | null = null
  #inFlight: Promise<void> | null = null

  constructor(options: SkillCatalogStoreOptions) {
    this.#gateway = options.gateway
    this.#clock = options.clock
    this.#logger = options.logger
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  getSnapshot = (): SkillCatalogSnapshot => this.#snapshot

  /** Reads the catalog unless a fresh one is loaded or a read is already running. */
  refresh(): Promise<void> {
    const now = this.#clock.now().epochMilliseconds
    if (this.#inFlight !== null) {
      return this.#inFlight
    }
    if (this.#loadedAt !== null && now - this.#loadedAt < SKILL_CATALOG_STALE_MS) {
      return Promise.resolve()
    }

    this.#set({ ...this.#snapshot, status: 'loading' })
    this.#inFlight = this.#load(now).finally(() => {
      this.#inFlight = null
    })
    return this.#inFlight
  }

  async #load(startedAt: number): Promise<void> {
    const result = await this.#gateway.list(AbortSignal.timeout(30_000))
    if (isOk(result)) {
      this.#loadedAt = startedAt
      this.#set({ entries: result.value, status: 'ready' })
      return
    }
    this.#logger.warn('skill_catalog_failed', { status: result.error.status, code: result.error.code })
    this.#set({ ...this.#snapshot, status: 'failed' })
  }

  #set(snapshot: SkillCatalogSnapshot): void {
    this.#snapshot = snapshot
    for (const listener of this.#listeners) {
      listener()
    }
  }
}
