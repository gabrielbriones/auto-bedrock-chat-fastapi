import { describe, expect, it, jest } from '@jest/globals'

import { invalidResponseProblem } from '@/shared/http/exception'
import { kbDocumentId } from '@/shared/kernel/branded'
import { ok, err, type Result } from '@/shared/kernel/result'
import type { Problem } from '@/shared/http/exception'

import type { KbQuery, KnowledgeGateway, Page } from '@/domains/knowledge/application/ports'
import { KnowledgeStore } from '@/domains/knowledge/application/knowledge.store'
import type { KbDocument, KbDocumentSummary } from '@/domains/knowledge/domain/public'
import { createCredibility } from '@/domains/knowledge/domain/public'
import { Instant } from '@/shared/kernel/instant'
import type { RollbackResult } from '@/shared/kernel/curation'
import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'
import { ScriptedConfirmationPort } from '../../../shared/ports/scripted-confirmation-port'

const query: KbQuery = {
  source: null,
  topic: null,
  tags: [],
  dateFrom: null,
  dateTo: null,
  removalFlagged: false,
  limit: 50,
  offset: 0,
}

const summary: KbDocumentSummary = {
  id: kbDocumentId('kb/1'),
  title: 'Guide',
  source: 'docs',
  sourceUrl: null,
  topic: 'compute',
  tags: [],
  chunkCount: 1,
  createdAt: null,
  credibility: createCredibility(0.8, false),
}

const document: KbDocument = {
  ...summary,
  content: 'Body text',
  datePublished: null,
  metadata: {},
}

const page: Page<KbDocumentSummary> = { items: [summary], total: 1, limit: 50, offset: 0 }

const rollbackResult: RollbackResult = { kbDocumentId: document.id, rolledBackAt: Instant.EPOCH }

const gatewayFor = (list: KnowledgeGateway['list']): KnowledgeGateway => ({
  list,
  get: jest.fn(),
  patch: jest.fn(),
  remove: jest.fn(),
  resetCredibility: jest.fn(),
  rollback: jest.fn(),
})

const gatewayWith = (overrides: Partial<KnowledgeGateway> = {}): KnowledgeGateway => ({
  list: jest.fn().mockResolvedValue(ok(page)),
  get: jest.fn().mockResolvedValue(ok(document)),
  patch: jest.fn().mockResolvedValue(ok(document)),
  remove: jest.fn().mockResolvedValue(ok(undefined)),
  resetCredibility: jest.fn().mockResolvedValue(ok(document)),
  rollback: jest.fn().mockResolvedValue(ok(rollbackResult)),
  ...overrides,
})

const createStore = (
  gateway: KnowledgeGateway,
  confirmations = new ScriptedConfirmationPort(),
  notifications = new RecordingNotificationPort(),
) => ({
  store: new KnowledgeStore({
    gateway,
    confirmations,
    notifications,
    logger: { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} },
  }),
  confirmations,
  notifications,
})

describe('KnowledgeStore', () => {
  it('publishes a loaded page and supports subscriptions', async () => {
    const list = jest.fn().mockResolvedValue(ok(page))
    const { store } = createStore(gatewayFor(list))
    const listener = jest.fn()
    const unsubscribe = store.subscribe(listener)

    await store.load(query)

    expect(store.getSnapshot()).toMatchObject({ page, listStatus: 'ready', listProblem: null })
    expect(listener).toHaveBeenCalledTimes(2)
    unsubscribe()
    store.dispose()
    expect(listener).toHaveBeenCalledTimes(2)
  })

  it('keeps the problem on a failed list request', async () => {
    const problem = invalidResponseProblem('bad list', [])
    const { store } = createStore(gatewayFor(jest.fn().mockResolvedValue(err(problem))))

    await store.load({ ...query, offset: 50 })

    expect(store.getSnapshot()).toMatchObject({ listStatus: 'error', listProblem: problem })
    expect(store.getSnapshot().page.offset).toBe(50)
  })

  it('ignores a response from a superseded request', async () => {
    let releaseFirst!: (result: Result<Page<KbDocumentSummary>, Problem>) => void
    const first = new Promise<Result<Page<KbDocumentSummary>, Problem>>((resolve) => {
      releaseFirst = resolve
    })
    const secondPage: Page<KbDocumentSummary> = { ...page, offset: 50 }
    const list = jest.fn().mockReturnValueOnce(first).mockResolvedValueOnce(ok(secondPage))
    const { store } = createStore(gatewayFor(list))

    const firstLoad = store.load(query)
    await store.load({ ...query, offset: 50 })
    releaseFirst(ok(page))
    await firstLoad

    expect(store.getSnapshot().page).toBe(secondPage)
  })
})

describe('KnowledgeStore detail lifecycle', () => {
  it('opens a document into the detail slot', async () => {
    const { store } = createStore(gatewayWith())

    await store.open(document.id)

    expect(store.getSnapshot()).toMatchObject({ activeDocument: document, detailStatus: 'ready', detailProblem: null })
  })

  it('records a detail load failure', async () => {
    const problem = invalidResponseProblem('bad get', [])
    const { store } = createStore(gatewayWith({ get: jest.fn().mockResolvedValue(err(problem)) }))

    await store.open(document.id)

    expect(store.getSnapshot()).toMatchObject({ detailStatus: 'error', detailProblem: problem })
  })

  it('resets detail state on close', async () => {
    const { store } = createStore(gatewayWith())

    await store.open(document.id)
    store.close()

    expect(store.getSnapshot()).toMatchObject({ activeDocument: null, detailStatus: 'idle', detailProblem: null })
  })
})

describe('KnowledgeStore save', () => {
  it('is a no-op for an empty patch', async () => {
    const patch = jest.fn()
    const { store } = createStore(gatewayWith({ patch }))

    const saved = await store.save(document.id, {})

    expect(saved).toBe(true)
    expect(patch).not.toHaveBeenCalled()
  })

  it('warns before a content change and aborts when declined', async () => {
    const patch = jest.fn()
    const { store, confirmations } = createStore(gatewayWith({ patch }), new ScriptedConfirmationPort([false]))

    const saved = await store.save(document.id, { content: 'new body' })

    expect(saved).toBe(false)
    expect(patch).not.toHaveBeenCalled()
    expect(confirmations.asked).toHaveLength(1)
  })

  it('saves a content change once the re-embed warning is confirmed', async () => {
    const patch = jest.fn().mockResolvedValue(ok(document))
    const { store, notifications } = createStore(gatewayWith({ patch }), new ScriptedConfirmationPort([true]))

    const saved = await store.save(document.id, { content: 'new body' })

    expect(saved).toBe(true)
    expect(patch).toHaveBeenCalledWith(document.id, { content: 'new body' })
    expect(notifications.messages()).toHaveLength(1)
  })

  it('does not warn for a non-content patch', async () => {
    const patch = jest.fn().mockResolvedValue(ok(document))
    const { store, confirmations } = createStore(gatewayWith({ patch }))

    const saved = await store.save(document.id, { title: 'New title' })

    expect(saved).toBe(true)
    expect(confirmations.asked).toHaveLength(0)
  })

  it('stores the problem without a toast on a conflict', async () => {
    const problem = { ...invalidResponseProblem('conflict', []), status: 409 }
    const { store, notifications } = createStore(gatewayWith({ patch: jest.fn().mockResolvedValue(err(problem)) }))

    const saved = await store.save(document.id, { title: 'New title' })

    expect(saved).toBe(false)
    expect(store.getSnapshot().saveProblem).toBe(problem)
    expect(notifications.messages()).toHaveLength(0)
  })

  it('refuses a concurrent save while one is pending', async () => {
    let release!: () => void
    const patch = jest.fn().mockReturnValue(new Promise((resolve) => {
      release = () => resolve(ok(document))
    }))
    const { store } = createStore(gatewayWith({ patch }))

    const first = store.save(document.id, { title: 'First' })
    const second = await store.save(document.id, { title: 'Second' })

    expect(second).toBe(false)
    release()
    await first
  })
})

describe('KnowledgeStore mutations', () => {
  it('resets credibility in place', async () => {
    const restored: KbDocument = { ...document, credibility: createCredibility(1, false) }
    const { store, notifications } = createStore(gatewayWith({ resetCredibility: jest.fn().mockResolvedValue(ok(restored)) }))
    await store.open(document.id)

    const succeeded = await store.resetCredibility(document.id)

    expect(succeeded).toBe(true)
    expect(store.getSnapshot().activeDocument).toBe(restored)
    expect(notifications.messages()).toHaveLength(1)
  })

  it('rolls back after confirmation and reloads', async () => {
    const load = jest.fn().mockResolvedValue(ok(page))
    const { store } = createStore(
      gatewayWith({ list: load, rollback: jest.fn().mockResolvedValue(ok(rollbackResult)) }),
      new ScriptedConfirmationPort([true]),
    )
    await store.load(query)
    await store.open(document.id)
    load.mockClear()

    const succeeded = await store.rollback(document.id)

    expect(succeeded).toBe(true)
    expect(store.getSnapshot().activeDocument).toBe(null)
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('deletes after confirmation and returns the previous offset when the last row is removed', async () => {
    const solo = { ...page, items: [summary], total: 6, offset: 5, limit: 1 }
    const load = jest.fn().mockResolvedValue(ok(solo))
    const { store } = createStore(
      gatewayWith({ list: load, remove: jest.fn().mockResolvedValue(ok(undefined)) }),
      new ScriptedConfirmationPort([true]),
    )
    await store.load({ ...query, limit: 1, offset: 5 })

    const previousOffset = await store.remove(document.id)

    expect(previousOffset).toBe(4)
  })

  it.each([
    ['rollback', false],
    ['remove', null],
  ] as const)('does not %s when declined', async (method, declinedResult) => {
    const mutation = jest.fn()
    const { store } = createStore(gatewayWith({ [method]: mutation }), new ScriptedConfirmationPort([false]))

    const result = await store[method](document.id)

    expect(result).toBe(declinedResult)
    expect(mutation).not.toHaveBeenCalled()
  })
})