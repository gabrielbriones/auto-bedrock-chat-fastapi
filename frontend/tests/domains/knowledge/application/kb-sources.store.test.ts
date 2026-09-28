import { describe, expect, it, jest } from '@jest/globals'

import type { Problem } from '@/shared/http/exception'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import { err, ok } from '@/shared/kernel/result'

import type { KbSourcesGateway } from '@/domains/knowledge/application/ports'
import { KbSourcesStore } from '@/domains/knowledge/application/kb-sources.store'
import { IDLE_RUN, type FileIngestRequest, type KbSourceRun, type WebCrawlRequest } from '@/domains/knowledge/domain/public'

import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'
import { ScriptedConfirmationPort } from '../../../shared/ports/scripted-confirmation-port'
import { FakePollScheduler } from './fake-poll-scheduler'

const SOURCES = KNOWLEDGE_COPY.sources

const sources = [{ source: 'feedback', count: 4 }, { source: 'intel-docs', count: 12 }]

const run = (overrides: Partial<KbSourceRun> = {}): KbSourceRun => ({
  ...IDLE_RUN,
  runId: 'run-1',
  phase: 'running',
  sourceName: 'intel-docs',
  sourceType: 'web',
  ...overrides,
})

const webRequest: WebCrawlRequest = {
  name: 'intel-docs',
  urls: ['https://example.com'],
  topic: null,
  maxDepth: 2,
  maxPages: 100,
  allowedDomains: null,
  excludePatterns: null,
  ingestLinkedFiles: false,
  headers: null,
  cookies: null,
}

const fileRequest: FileIngestRequest = {
  name: 'notes',
  topic: null,
  files: [new File(['hello'], 'notes.txt', { type: 'text/plain' })],
}

const problem = (overrides: Partial<Problem>): Problem => ({ code: 'http-error', title: 'Request failed', ...overrides })
const duplicate = problem({ status: 409, serverCode: 'source_already_exists', detail: 'already has 4 document(s)' })
const inProgress = problem({ status: 409, serverCode: 'kb_source_run_already_in_progress' })

const gatewayWith = (overrides: Partial<KbSourcesGateway> = {}): KbSourcesGateway => ({
  listSources: jest.fn(async () => ok(sources)),
  status: jest.fn(async () => ok(IDLE_RUN)),
  startWebCrawl: jest.fn(async () => ok(run())),
  overrideWebCrawl: jest.fn(async () => ok(run({ runId: 'run-2' }))),
  startFileIngest: jest.fn(async () => ok(run({ sourceType: 'file', sourceName: 'notes' }))),
  overrideFileIngest: jest.fn(async () => ok(run({ runId: 'run-2', sourceType: 'file', sourceName: 'notes' }))),
  deleteSource: jest.fn(async () => ok({ source: 'feedback', deleted: 4 })),
  ...overrides,
})

const createStore = (gateway: KbSourcesGateway, answers: readonly boolean[] = []) => {
  const confirmations = new ScriptedConfirmationPort(answers)
  const notifications = new RecordingNotificationPort()
  const scheduler = new FakePollScheduler()
  const store = new KbSourcesStore({
    gateway,
    confirmations,
    notifications,
    scheduler,
    logger: { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} },
    pollIntervalMs: 250,
  })

  return { store, confirmations, notifications, scheduler }
}

describe('KbSourcesStore load', () => {
  it('loads the source list and the run status together, and does not poll an idle run', async () => {
    const { store, scheduler } = createStore(gatewayWith())
    const listener = jest.fn()
    store.subscribe(listener)

    await store.load()

    expect(store.getSnapshot()).toMatchObject({ sources, sourcesStatus: 'ready', run: IDLE_RUN, runProblem: null })
    expect(scheduler.pendingCount).toBe(0)
    expect(listener).toHaveBeenCalled()
  })

  it('keeps the list problem and leaves the run readable when only the list fails', async () => {
    const failure = problem({ status: 500 })
    const { store } = createStore(gatewayWith({ listSources: jest.fn(async () => err(failure)) }))

    await store.load()

    expect(store.getSnapshot()).toMatchObject({ sourcesStatus: 'error', sourcesProblem: failure, run: IDLE_RUN })
  })

  it('picks up a run already in progress and polls it to completion', async () => {
    const status = jest.fn<KbSourcesGateway['status']>()
      .mockResolvedValueOnce(ok(run()))
      .mockResolvedValueOnce(ok(run({ pagesProcessed: 3 })))
      .mockResolvedValueOnce(ok(run({ phase: 'completed', chunksWritten: 41 })))
    const listSources = jest.fn(async () => ok(sources))
    const { store, scheduler, notifications } = createStore(gatewayWith({ status, listSources }))

    await store.load()
    expect(scheduler.pendingCount).toBe(1)
    expect(scheduler.delays).toEqual([250])

    await scheduler.flush()
    expect(store.getSnapshot().run.pagesProcessed).toBe(3)
    expect(scheduler.pendingCount).toBe(1)

    await scheduler.flush()

    expect(store.getSnapshot().run.phase).toBe('completed')
    expect(scheduler.pendingCount).toBe(0)
    expect(notifications.notifications).toEqual([{ kind: 'success', message: SOURCES.run.finished(41) }])
    // The list refreshes once the run has left documents behind.
    expect(listSources).toHaveBeenCalledTimes(2)
  })

  it('does not announce a run that was already over when the page opened', async () => {
    const { store, scheduler, notifications } = createStore(
      gatewayWith({ status: jest.fn(async () => ok(run({ phase: 'completed' }))) }),
    )

    await store.load()

    expect(store.getSnapshot().run.phase).toBe('completed')
    expect(scheduler.pendingCount).toBe(0)
    expect(notifications.messages()).toEqual([])
  })

  it('stops polling and records the problem when a status read fails', async () => {
    const failure = problem({ status: 503 })
    const status = jest.fn<KbSourcesGateway['status']>().mockResolvedValueOnce(ok(run())).mockResolvedValueOnce(err(failure))
    const { store, scheduler } = createStore(gatewayWith({ status }))

    await store.load()
    await scheduler.flush()

    expect(store.getSnapshot().runProblem).toBe(failure)
    expect(scheduler.pendingCount).toBe(0)
  })

  it('cancels a scheduled poll on dispose', async () => {
    const { store, scheduler } = createStore(gatewayWith({ status: jest.fn(async () => ok(run())) }))

    await store.load()
    store.dispose()

    expect(scheduler.pendingCount).toBe(0)
  })
})

describe('KbSourcesStore run outcomes', () => {
  const finishWith = async (final: KbSourceRun) => {
    const status = jest.fn<KbSourcesGateway['status']>().mockResolvedValueOnce(ok(run())).mockResolvedValueOnce(ok(final))
    const created = createStore(gatewayWith({ status }))
    await created.store.load()
    await created.scheduler.flush()
    return created
  }

  it('reports item errors on a completed run as information', async () => {
    const { notifications } = await finishWith(run({ phase: 'completed', chunksWritten: 2, errors: ['a', 'b'] }))

    expect(notifications.notifications).toEqual([{ kind: 'info', message: SOURCES.run.finishedWithErrors(2, 2) }])
  })

  it('reports a failed run with its error, or a placeholder when none was given', async () => {
    const withError = await finishWith(run({ phase: 'failed', error: 'embedding client down' }))
    const withoutError = await finishWith(run({ phase: 'failed' }))

    expect(withError.notifications.notifications).toEqual([{ kind: 'error', message: SOURCES.run.failed('embedding client down') }])
    expect(withoutError.notifications.messages()).toEqual([SOURCES.run.failed(SOURCES.run.unknownError)])
  })
})

describe('KbSourcesStore starting a run', () => {
  it('starts a web crawl, announces the run id, shows the run and polls it', async () => {
    const gateway = gatewayWith()
    const { store, scheduler, notifications, confirmations } = createStore(gateway)

    const started = await store.startWebCrawl(webRequest)

    expect(started).toBe(true)
    expect(gateway.startWebCrawl).toHaveBeenCalledWith(webRequest)
    expect(notifications.notifications).toEqual([{ kind: 'success', message: SOURCES.web.started('run-1') }])
    expect(store.getSnapshot()).toMatchObject({ run: run(), submitting: null })
    expect(scheduler.pendingCount).toBe(1)
    expect(confirmations.asked).toHaveLength(0)
  })

  it('marks which form is submitting while the request is in flight', async () => {
    let release!: () => void
    const startFileIngest = jest.fn<KbSourcesGateway['startFileIngest']>().mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve(ok(run({ sourceType: 'file' })))
      }),
    )
    const { store } = createStore(gatewayWith({ startFileIngest }))

    const pending = store.startFileIngest(fileRequest)
    expect(store.getSnapshot().submitting).toBe('file')

    // A second submission while one is pending is refused rather than queued.
    expect(await store.startWebCrawl(webRequest)).toBe(false)

    release()
    expect(await pending).toBe(true)
    expect(store.getSnapshot().submitting).toBe(null)
  })

  it('offers to override an existing source and re-submits as PUT when confirmed', async () => {
    const gateway = gatewayWith({ startWebCrawl: jest.fn(async () => err(duplicate)) })
    const { store, confirmations, notifications } = createStore(gateway, [true])

    const started = await store.startWebCrawl(webRequest)

    expect(started).toBe(true)
    expect(confirmations.asked).toEqual([
      {
        title: SOURCES.override.title,
        message: SOURCES.override.message('intel-docs'),
        confirmLabel: SOURCES.override.confirm,
        tone: 'destructive',
      },
    ])
    expect(gateway.overrideWebCrawl).toHaveBeenCalledWith(webRequest)
    expect(notifications.notifications).toEqual([{ kind: 'success', message: SOURCES.web.overrideStarted('run-2') }])
  })

  it('does nothing further when the override is declined', async () => {
    const gateway = gatewayWith({ startFileIngest: jest.fn(async () => err(duplicate)) })
    const { store, notifications } = createStore(gateway, [false])

    const started = await store.startFileIngest(fileRequest)

    expect(started).toBe(false)
    expect(gateway.overrideFileIngest).not.toHaveBeenCalled()
    expect(notifications.messages()).toEqual([])
    expect(store.getSnapshot().submitting).toBe(null)
  })

  it('uses the file-upload wording for an overridden file ingestion', async () => {
    const gateway = gatewayWith({ startFileIngest: jest.fn(async () => err(duplicate)) })
    const { store, notifications } = createStore(gateway, [true])

    await store.startFileIngest(fileRequest)

    expect(gateway.overrideFileIngest).toHaveBeenCalledWith(fileRequest)
    expect(notifications.messages()).toEqual([SOURCES.file.overrideStarted('run-2')])
  })

  it('shows the in-flight run instead of an error when another run holds the lock', async () => {
    const gateway = gatewayWith({ startWebCrawl: jest.fn(async () => err(inProgress)) })
    const { store, scheduler, notifications, confirmations } = createStore(gateway)

    const started = await store.startWebCrawl(webRequest)

    expect(started).toBe(false)
    expect(confirmations.asked).toHaveLength(0)
    expect(notifications.notifications).toEqual([{ kind: 'info', message: SOURCES.run.inProgress }])
    expect(scheduler.pendingCount).toBe(1)
  })

  it('reports any other failure with the server detail', async () => {
    const failure = problem({ status: 422, serverCode: 'invalid_pdf_file', detail: 'report.pdf is encrypted' })
    const { store, notifications } = createStore(gatewayWith({ startFileIngest: jest.fn(async () => err(failure)) }))

    const started = await store.startFileIngest(fileRequest)

    expect(started).toBe(false)
    expect(notifications.notifications).toEqual([
      { kind: 'error', message: SOURCES.file.failure, options: { description: 'report.pdf is encrypted' } },
    ])
  })

  it('reports a failure without detail as the bare message', async () => {
    const { store, notifications } = createStore(gatewayWith({ startWebCrawl: jest.fn(async () => err(problem({ status: 503 }))) }))

    await store.startWebCrawl(webRequest)

    expect(notifications.notifications).toEqual([{ kind: 'error', message: SOURCES.web.failure }])
  })
})

describe('KbSourcesStore deleting a source', () => {
  it('deletes after confirmation, reports the server count and reloads the list', async () => {
    const gateway = gatewayWith()
    const { store, confirmations, notifications } = createStore(gateway, [true])
    await store.loadSources()

    const deleted = await store.deleteSource({ source: 'feedback', count: 4 })

    expect(deleted).toBe(true)
    expect(confirmations.asked[0]).toMatchObject({
      title: SOURCES.list.deleteConfirmTitle,
      message: SOURCES.list.deleteConfirmMessage('feedback', 4),
      tone: 'destructive',
    })
    expect(gateway.deleteSource).toHaveBeenCalledWith('feedback')
    expect(notifications.messages()).toEqual([SOURCES.list.deleteSuccess('feedback', 4)])
    expect(gateway.listSources).toHaveBeenCalledTimes(2)
    expect(store.getSnapshot().deleting).toBe(null)
  })

  it('falls back to the row count when the server omits one', async () => {
    const gateway = gatewayWith({ deleteSource: jest.fn(async () => ok({ source: 'feedback', deleted: null })) })
    const { store, notifications } = createStore(gateway, [true])

    await store.deleteSource({ source: 'feedback', count: 7 })

    expect(notifications.messages()).toEqual([SOURCES.list.deleteSuccess('feedback', 7)])
  })

  it('does not delete when declined', async () => {
    const gateway = gatewayWith()
    const { store } = createStore(gateway, [false])

    expect(await store.deleteSource({ source: 'feedback', count: 4 })).toBe(false)
    expect(gateway.deleteSource).not.toHaveBeenCalled()
  })

  it('reports a failed delete and clears the pending marker', async () => {
    const failure = problem({ status: 404, detail: 'no documents found' })
    const gateway = gatewayWith({ deleteSource: jest.fn(async () => err(failure)) })
    const { store, notifications } = createStore(gateway, [true])

    expect(await store.deleteSource({ source: 'feedback', count: 4 })).toBe(false)
    expect(notifications.notifications).toEqual([
      { kind: 'error', message: SOURCES.list.deleteFailure, options: { description: 'no documents found' } },
    ])
    expect(store.getSnapshot().deleting).toBe(null)
  })

  it('refuses a second delete while one is in flight', async () => {
    let release!: () => void
    const deleteSource = jest.fn<KbSourcesGateway['deleteSource']>().mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve(ok({ source: 'feedback', deleted: 4 }))
      }),
    )
    const { store } = createStore(gatewayWith({ deleteSource }), [true])

    const first = store.deleteSource({ source: 'feedback', count: 4 })
    await Promise.resolve()
    expect(await store.deleteSource({ source: 'intel-docs', count: 12 })).toBe(false)

    release()
    expect(await first).toBe(true)
  })
})
