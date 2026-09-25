import { describe, expect, it, jest } from '@jest/globals'

import { err, type Result } from '@/shared/kernel/result'
import { ok } from '@/shared/kernel/result'
import type { Problem } from '@/shared/http/exception'
import { feedbackEntryId, kbDocumentId } from '@/shared/kernel/branded'
import { Instant } from '@/shared/kernel/instant'
import type {
  FeedbackStats,
  Page,
  ReviewGateway,
  ReviewQuery,
  RollbackOutcome,
  SynthesisOutcome,
} from '@/domains/review/application/ports'
import { ReviewStore } from '@/domains/review/application/review.store'
import type { FeedbackEntry, FeedbackEntrySummary } from '@/domains/review/domain/public'
import { noFilters } from '@/domains/review/domain/public'
import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'
import { ScriptedConfirmationPort } from '../../../shared/ports/scripted-confirmation-port'

import { anEntry, aSummary } from '../domain/feedback-entry.fixture'

type PendingList = {
  readonly query: ReviewQuery
  readonly signal: AbortSignal
  readonly resolve: (result: Result<Page<FeedbackEntrySummary>, Problem>) => void
}

const query = (offset: number): ReviewQuery => ({ ...noFilters, limit: 50, offset })

const deferredGateway = () => {
  const pending: PendingList[] = []
  const list = jest.fn(
    (requested: ReviewQuery, signal: AbortSignal) =>
      new Promise<Result<Page<FeedbackEntrySummary>, Problem>>((resolve) => {
        pending.push({ query: requested, signal, resolve })
      }),
  )

  return { gateway: { list } as unknown as ReviewGateway, pending }
}

const createStore = (
  gateway: ReviewGateway,
  confirmations = new ScriptedConfirmationPort(),
  notifications = new RecordingNotificationPort(),
) => ({
  store: new ReviewStore({
    gateway,
    confirmations,
    notifications,
    logger: { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} },
  }),
  confirmations,
  notifications,
})

const page = (
  items: readonly FeedbackEntrySummary[],
  offset = 0,
  total = items.length,
): Page<FeedbackEntrySummary> => ({ items, total, limit: 50, offset })

const gatewayWith = (overrides: Partial<ReviewGateway> = {}): ReviewGateway =>
  ({
    list: jest.fn().mockResolvedValue(ok(page([]))),
    get: jest.fn(),
    saveDecision: jest.fn(),
    remove: jest.fn(),
    stats: jest.fn(),
    pendingCount: jest.fn().mockResolvedValue(ok(0)),
    synthesize: jest.fn(),
    rollback: jest.fn(),
    synthesisPhase: jest.fn().mockResolvedValue(ok('idle')),
    ...overrides,
  }) as ReviewGateway

describe('ReviewStore list sequencing', () => {
  it('aborts the superseded request and ignores it even when the gateway resolves it later', async () => {
    const { gateway, pending } = deferredGateway()
    const { store } = createStore(gateway)
    const first = store.load(query(0))
    const second = store.load(query(50))

    expect(pending[0]?.signal.aborted).toBe(true)
    expect(pending[1]?.signal.aborted).toBe(false)

    pending[1]?.resolve(ok({ items: [aSummary('new')], total: 51, limit: 50, offset: 50 }))
    await second
    pending[0]?.resolve(ok({ items: [aSummary('stale')], total: 1, limit: 50, offset: 0 }))
    await first

    expect(store.getSnapshot().queue.entries.map((entry) => entry.id)).toEqual(['new'])
    expect(store.getSnapshot().queue.page.offset).toBe(50)
  })
})

describe('ReviewStore bulk delete', () => {
  it('keeps approved entries out of selection', async () => {
    const rejected = aSummary('rejected', 'rejected')
    const approved = aSummary('approved', 'approved')
    const gateway = gatewayWith({ list: jest.fn().mockResolvedValue(ok(page([rejected, approved]))) })
    const { store } = createStore(gateway)

    await store.load(query(0))
    store.toggleSelected(approved.id)
    store.toggleSelected(rejected.id)

    expect([...store.getSnapshot().queue.selection]).toEqual([rejected.id])
  })

  it('deletes concurrently, names failed entries, and refetches instead of patching the page', async () => {
    const first = aSummary('failed-id', 'rejected')
    const second = aSummary('deleted-id', 'rejected')
    const list = jest
      .fn()
      .mockResolvedValueOnce(ok(page([first, second])))
      .mockResolvedValueOnce(ok(page([first], 0, 1)))
    const remove = jest.fn(async (id: FeedbackEntrySummary['id']) =>
      id === first.id
        ? err<Problem>({ code: 'http-error', title: 'Conflict', status: 409 })
        : ok(undefined),
    )
    const gateway = gatewayWith({ list, remove })
    const confirmations = new ScriptedConfirmationPort([true])
    const notifications = new RecordingNotificationPort()
    const { store } = createStore(gateway, confirmations, notifications)

    await store.load(query(0))
    store.toggleSelectAll()
    const outcome = await store.removeSelected()

    expect(remove).toHaveBeenCalledTimes(2)
    expect(list).toHaveBeenCalledTimes(2)
    expect(store.getSnapshot().queue.total).toBe(1)
    expect(store.getSnapshot().queue.entries.map((entry) => entry.id)).toEqual(['failed-id'])
    expect(outcome).toMatchObject({ deleted: 1, failedIds: ['failed-id'] })
    expect(notifications.notifications.at(-1)).toMatchObject({
      kind: 'error',
      options: { description: 'Failed: failed-id', durationMs: 0 },
    })
  })

  it('returns the preceding offset when refetch finds the current page empty', async () => {
    const rejected = aSummary('last-row', 'rejected')
    const list = jest
      .fn()
      .mockResolvedValueOnce(ok(page([rejected], 50, 51)))
      .mockResolvedValueOnce(ok(page([], 50, 50)))
    const gateway = gatewayWith({ list, remove: jest.fn().mockResolvedValue(ok(undefined)) })
    const { store } = createStore(gateway, new ScriptedConfirmationPort([true]))

    await store.load(query(50))
    store.toggleSelected(rejected.id)
    const outcome = await store.removeSelected()

    expect(outcome?.previousOffset).toBe(0)
  })

  it('does nothing when confirmation is cancelled', async () => {
    const rejected = aSummary('rejected', 'rejected')
    const remove = jest.fn()
    const gateway = gatewayWith({ list: jest.fn().mockResolvedValue(ok(page([rejected]))), remove })
    const { store } = createStore(gateway, new ScriptedConfirmationPort([false]))

    await store.load(query(0))
    store.toggleSelected(rejected.id)

    expect(await store.removeSelected()).toBeNull()
    expect(remove).not.toHaveBeenCalled()
  })
})

describe('ReviewStore save', () => {
  it('closes the entry and refetches after a successful decision', async () => {
    const summary = aSummary('entry', 'pending_review')
    const entry = { ...summary, sessionId: 'session', modelId: 'model', aiResponse: 'answer' } as FeedbackEntry
    const list = jest.fn().mockResolvedValue(ok(page([summary])))
    const gateway = gatewayWith({
      list,
      get: jest.fn().mockResolvedValue(ok(entry)),
      saveDecision: jest.fn().mockResolvedValue(ok(entry)),
    })
    const { store, notifications } = createStore(gateway)

    await store.load(query(0))
    await store.open(summary.id)
    const saved = await store.saveDecision(summary.id, {
      decision: 'approved',
      tags: [],
      comment: null,
    })

    expect(saved).toBe(true)
    expect(store.getSnapshot().activeEntry).toBeNull()
    expect(list).toHaveBeenCalledTimes(2)
    expect(notifications.messages()).toContain('Review saved.')
  })

  it('refuses a decision change while a synthesis mutation is pending', async () => {
    const synthesize = jest.fn(() => new Promise<Result<SynthesisOutcome, Problem>>(() => {}))
    const saveDecision = jest.fn()
    const gateway = gatewayWith({ synthesize, saveDecision, get: jest.fn().mockResolvedValue(ok(anEntry())) })
    const { store } = createStore(gateway)

    void store.synthesize(feedbackEntryId('id-1'))
    const saved = await store.saveDecision(feedbackEntryId('id-1'), {
      decision: 'approved',
      tags: [],
      comment: null,
    })

    expect(saved).toBe(false)
    expect(saveDecision).not.toHaveBeenCalled()
  })
})

describe('ReviewStore open synthesis phase', () => {
  it('tolerates a synthesisPhase failure and leaves the phase null', async () => {
    const entry = anEntry()
    const gateway = gatewayWith({
      get: jest.fn().mockResolvedValue(ok(entry)),
      synthesisPhase: jest.fn().mockResolvedValue(err<Problem>({ code: 'http-error', title: 'Down', status: 503 })),
    })
    const { store } = createStore(gateway)

    await store.open(entry.id)

    expect(store.getSnapshot().detailStatus).toBe('ready')
    expect(store.getSnapshot().synthesisPhase).toBeNull()
  })
})

describe('ReviewStore synthesize', () => {
  it('notifies, reopens the entry, and refreshes the pending count on success', async () => {
    const entry = anEntry()
    const outcome: SynthesisOutcome = {
      tag: 'emon',
      action: 'created',
      kbDocumentId: kbDocumentId('kb-1'),
      markedEntryIds: [],
    }
    const gateway = gatewayWith({
      get: jest.fn().mockResolvedValue(ok(entry)),
      synthesize: jest.fn().mockResolvedValue(ok(outcome)),
      pendingCount: jest.fn().mockResolvedValue(ok(2)),
    })
    const { store, notifications } = createStore(gateway)

    const result = await store.synthesize(entry.id)

    expect(result).toBe(true)
    expect(store.getSnapshot().synthesisPending).toBe(false)
    expect(store.getSnapshot().activeEntry).toEqual(entry)
    expect(store.getSnapshot().pendingCount).toBe(2)
    expect(notifications.messages()).toContain('Synthesized into kb-1.')
  })

  it('records a synthesis problem on failure without reopening', async () => {
    const problem: Problem = { code: 'http-error', title: 'Conflict', status: 409 }
    const gateway = gatewayWith({ synthesize: jest.fn().mockResolvedValue(err(problem)) })
    const { store } = createStore(gateway)

    const result = await store.synthesize(feedbackEntryId('id-1'))

    expect(result).toBe(false)
    expect(store.getSnapshot().synthesisPending).toBe(false)
    expect(store.getSnapshot().synthesisProblem).toEqual({ kind: 'synthesize', problem })
  })

  it('ignores a concurrent call while one is already pending', async () => {
    let resolve!: (value: Result<SynthesisOutcome, Problem>) => void
    const synthesize = jest.fn(
      () => new Promise<Result<SynthesisOutcome, Problem>>((res) => { resolve = res }),
    )
    const gateway = gatewayWith({ synthesize, get: jest.fn().mockResolvedValue(ok(anEntry())) })
    const { store } = createStore(gateway)

    const first = store.synthesize(feedbackEntryId('id-1'))
    const second = await store.synthesize(feedbackEntryId('id-1'))

    expect(second).toBe(false)
    expect(synthesize).toHaveBeenCalledTimes(1)

    resolve(ok({ tag: 'emon', action: 'created', kbDocumentId: null, markedEntryIds: [] }))
    await first
  })

  it('skips revalidation when the drawer moved on while the trigger was in flight', async () => {
    let resolve!: (value: Result<SynthesisOutcome, Problem>) => void
    const entry = anEntry()
    const get = jest.fn().mockResolvedValue(ok(entry))
    const gateway = gatewayWith({
      get,
      synthesize: jest.fn(() => new Promise<Result<SynthesisOutcome, Problem>>((res) => { resolve = res })),
    })
    const { store } = createStore(gateway)

    await store.open(entry.id)
    const pending = store.synthesize(entry.id)
    store.close()
    resolve(ok({ tag: 'emon', action: 'created', kbDocumentId: null, markedEntryIds: [] }))

    expect(await pending).toBe(true)
    expect(get).toHaveBeenCalledTimes(1)
    expect(store.getSnapshot().activeEntry).toBeNull()
  })
})

describe('ReviewStore rollback', () => {
  const outcome: RollbackOutcome = {
    kbDocumentId: kbDocumentId('kb-1'),
    rolledBackAt: Instant.EPOCH,
    rolledBackBy: 'rzhang',
    reason: null,
    entriesReverted: 2,
  }

  it('sends no reason when the prompt is left blank and the follow-up confirm accepts', async () => {
    const rollback = jest.fn().mockResolvedValue(ok(outcome))
    const gateway = gatewayWith({ rollback })
    const confirmations = new ScriptedConfirmationPort(['', true])
    const { store, notifications } = createStore(gateway, confirmations)

    const result = await store.rollback(kbDocumentId('kb-1'))

    expect(result).toBe(true)
    expect(rollback).toHaveBeenCalledWith(kbDocumentId('kb-1'), null)
    expect(notifications.messages()).toContain('Rolled back. 2 feedback entries reverted.')
    expect(store.getSnapshot().activeEntry).toBeNull()
  })

  it('sends the trimmed reason without a follow-up confirm when one is given', async () => {
    const rollback = jest.fn().mockResolvedValue(ok({ ...outcome, reason: 'Superseded.' }))
    const gateway = gatewayWith({ rollback })
    const confirmations = new ScriptedConfirmationPort([' Superseded. '])
    const { store } = createStore(gateway, confirmations)

    await store.rollback(kbDocumentId('kb-1'))

    expect(rollback).toHaveBeenCalledWith(kbDocumentId('kb-1'), 'Superseded.')
  })

  it('does nothing when the reason prompt is cancelled', async () => {
    const rollback = jest.fn()
    const gateway = gatewayWith({ rollback })
    const confirmations = new ScriptedConfirmationPort([null])
    const { store } = createStore(gateway, confirmations)

    expect(await store.rollback(kbDocumentId('kb-1'))).toBe(false)
    expect(rollback).not.toHaveBeenCalled()
  })

  it('does nothing when the blank-reason confirm is declined', async () => {
    const rollback = jest.fn()
    const gateway = gatewayWith({ rollback })
    const confirmations = new ScriptedConfirmationPort(['', false])
    const { store } = createStore(gateway, confirmations)

    expect(await store.rollback(kbDocumentId('kb-1'))).toBe(false)
    expect(rollback).not.toHaveBeenCalled()
  })

  it('records a synthesis problem on failure', async () => {
    const problem: Problem = { code: 'http-error', title: 'Conflict', status: 409 }
    const gateway = gatewayWith({ rollback: jest.fn().mockResolvedValue(err(problem)) })
    const confirmations = new ScriptedConfirmationPort(['reason'])
    const { store } = createStore(gateway, confirmations)

    const result = await store.rollback(kbDocumentId('kb-1'))

    expect(result).toBe(false)
    expect(store.getSnapshot().synthesisProblem).toEqual({ kind: 'rollback', problem })
  })

  it('ignores a call while a synthesis mutation is already pending', async () => {
    let resolveSynthesize!: (value: Result<SynthesisOutcome, Problem>) => void
    const synthesize = jest.fn(
      () => new Promise<Result<SynthesisOutcome, Problem>>((res) => { resolveSynthesize = res }),
    )
    const rollback = jest.fn()
    const gateway = gatewayWith({ synthesize, rollback, get: jest.fn().mockResolvedValue(ok(anEntry())) })
    const { store } = createStore(gateway)

    const pendingSynthesize = store.synthesize(feedbackEntryId('id-1'))

    expect(await store.rollback(kbDocumentId('kb-1'))).toBe(false)
    expect(rollback).not.toHaveBeenCalled()

    resolveSynthesize(ok({ tag: 'emon', action: 'created', kbDocumentId: null, markedEntryIds: [] }))
    await pendingSynthesize
  })
})

describe('ReviewStore stats', () => {
  const stats: FeedbackStats = {
    total: 10,
    pendingReview: 4,
    approved: 4,
    rejected: 2,
    positive: 6,
    negative: 4,
    withCorrection: 3,
    integrated: 2,
    oldestPendingHours: 12,
    topTags: [{ tag: 'vectorization', count: 3 }],
  }

  it('loads stats on success', async () => {
    const gateway = gatewayWith({ stats: jest.fn().mockResolvedValue(ok(stats)) })
    const { store } = createStore(gateway)

    await store.loadStats(new AbortController().signal)

    expect(store.getSnapshot().statsStatus).toBe('ready')
    expect(store.getSnapshot().stats).toEqual(stats)
  })

  it('records a problem on failure', async () => {
    const problem: Problem = { code: 'http-error', title: 'Down', status: 503 }
    const gateway = gatewayWith({ stats: jest.fn().mockResolvedValue(err(problem)) })
    const { store } = createStore(gateway)

    await store.loadStats(new AbortController().signal)

    expect(store.getSnapshot().statsStatus).toBe('error')
    expect(store.getSnapshot().statsProblem).toEqual(problem)
  })

  it('discards a resolved response once its signal is aborted', async () => {
    let resolve!: (value: Result<FeedbackStats, Problem>) => void
    const gateway = gatewayWith({
      stats: jest.fn(() => new Promise<Result<FeedbackStats, Problem>>((res) => { resolve = res })),
    })
    const { store } = createStore(gateway)
    const controller = new AbortController()

    const load = store.loadStats(controller.signal)
    controller.abort()
    resolve(ok(stats))
    await load

    expect(store.getSnapshot().statsStatus).toBe('loading')
  })

  it('discards a superseded response that resolves after the newer one', async () => {
    const resolvers: ((value: Result<FeedbackStats, Problem>) => void)[] = []
    const gateway = gatewayWith({
      stats: jest.fn(() => new Promise<Result<FeedbackStats, Problem>>((res) => { resolvers.push(res) })),
    })
    const { store } = createStore(gateway)

    const first = store.loadStats(new AbortController().signal)
    const second = store.loadStats(new AbortController().signal)
    resolvers[1]?.(ok(stats))
    await second
    resolvers[0]?.(ok({ ...stats, total: 999 }))
    await first

    expect(store.getSnapshot().stats?.total).toBe(stats.total)
  })
})

describe('ReviewStore pending count', () => {
  it('updates the pending count on success', async () => {
    const gateway = gatewayWith({ pendingCount: jest.fn().mockResolvedValue(ok(7)) })
    const { store } = createStore(gateway)

    await store.refreshPendingCount()

    expect(store.getSnapshot().pendingCount).toBe(7)
  })

  it('silently ignores a failure', async () => {
    const problem: Problem = { code: 'http-error', title: 'Down', status: 503 }
    const gateway = gatewayWith({ pendingCount: jest.fn().mockResolvedValue(err(problem)) })
    const { store } = createStore(gateway)

    await store.refreshPendingCount()

    expect(store.getSnapshot().pendingCount).toBeNull()
  })

  it('ignores a superseded probe that resolves after the newer one', async () => {
    const resolvers: ((value: Result<number, Problem>) => void)[] = []
    const gateway = gatewayWith({
      pendingCount: jest.fn(() => new Promise<Result<number, Problem>>((res) => { resolvers.push(res) })),
    })
    const { store } = createStore(gateway)

    const first = store.refreshPendingCount()
    const second = store.refreshPendingCount()
    resolvers[1]?.(ok(3))
    await second
    resolvers[0]?.(ok(99))
    await first

    expect(store.getSnapshot().pendingCount).toBe(3)
  })
})