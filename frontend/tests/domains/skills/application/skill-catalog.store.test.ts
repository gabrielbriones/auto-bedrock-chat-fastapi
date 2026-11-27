import { describe, expect, it, jest } from '@jest/globals'

import { err, ok } from '@/shared/kernel/result'
import { Instant, type Clock } from '@/shared/kernel/instant'
import type { Logger } from '@/shared/logging/logger'

import { SKILL_CATALOG_STALE_MS, SkillCatalogStore } from '@/domains/skills/application/skill-catalog.store'
import type { SkillCatalogGateway } from '@/domains/skills/application/ports'
import type { SkillCatalogEntry } from '@/domains/skills/domain/public'

const ENTRY: SkillCatalogEntry = { name: 'triage', displayName: 'Triage', description: '', parameters: [], revision: 1 }

class MovableClock implements Clock {
  millis = 1_000

  now(): Instant {
    const instant = Instant.fromEpochMilliseconds(this.millis)
    if (instant.kind !== 'ok') {
      throw new Error('bad instant')
    }
    return instant.value
  }
}

const silentLogger = (): Logger => ({ debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() }) as unknown as Logger

const setup = (list: SkillCatalogGateway['list']) => {
  const gateway = { list: jest.fn(list) }
  const clock = new MovableClock()
  const store = new SkillCatalogStore({ gateway, clock, logger: silentLogger() })
  return { gateway, clock, store }
}

describe('SkillCatalogStore', () => {
  it('loads the catalog and reuses it until it is stale', async () => {
    const { gateway, clock, store } = setup(async () => ok([ENTRY]))

    await store.refresh()
    await store.refresh()
    expect(store.getSnapshot()).toEqual({ entries: [ENTRY], status: 'ready' })
    expect(gateway.list).toHaveBeenCalledTimes(1)

    clock.millis += SKILL_CATALOG_STALE_MS
    await store.refresh()
    expect(gateway.list).toHaveBeenCalledTimes(2)
  })

  it('shares one read between concurrent refreshes', async () => {
    const { gateway, store } = setup(async () => ok([ENTRY]))

    await Promise.all([store.refresh(), store.refresh()])

    expect(gateway.list).toHaveBeenCalledTimes(1)
  })

  it('keeps the last entries when a read fails, and retries on the next refresh', async () => {
    const results = [ok([ENTRY]), err({ code: 'network-error' as const, title: 'offline' })]
    const { gateway, clock, store } = setup(async () => results.shift() ?? ok([]))

    await store.refresh()
    clock.millis += SKILL_CATALOG_STALE_MS
    await store.refresh()

    expect(store.getSnapshot()).toEqual({ entries: [ENTRY], status: 'failed' })
    await store.refresh()
    expect(gateway.list).toHaveBeenCalledTimes(3)
  })

  it('fails, keeping its entries, when the read itself throws', async () => {
    const results: Array<() => ReturnType<SkillCatalogGateway['list']>> = [
      async () => ok([ENTRY]),
      () => Promise.reject(new Error('body timeout')),
    ]
    const { clock, store } = setup(() => (results.shift() ?? (async () => ok([])))())

    await store.refresh()
    clock.millis += SKILL_CATALOG_STALE_MS
    await store.refresh()

    expect(store.getSnapshot()).toEqual({ entries: [ENTRY], status: 'failed' })
  })

  it('notifies subscribers', async () => {
    const { store } = setup(async () => ok([ENTRY]))
    const listener = jest.fn()
    store.subscribe(listener)

    await store.refresh()

    expect(listener).toHaveBeenCalledTimes(2)
  })
})
