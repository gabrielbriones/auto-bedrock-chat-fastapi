import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { describe, expect, it, jest } from '@jest/globals'
import { axe } from 'jest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import type { Container } from '@/app/bootstrap/container'
import { ConfirmationHost } from '@/app/providers/ConfirmationHost'
import { createAppRouter } from '@/app/router'
import type { KbSourcesGateway } from '@/domains/knowledge/application/ports'
import { KbSourcesStore } from '@/domains/knowledge/application/kb-sources.store'
import { IDLE_RUN, type KbSourceRun } from '@/domains/knowledge/domain/public'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import { SHELL } from '@/shared/copy/shell'
import type { Problem } from '@/shared/http/exception'
import { err, ok } from '@/shared/kernel/result'

import { fakeContainer } from '../../../app/bootstrap/container.fixture'
import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'
import { FakePollScheduler } from '../application/fake-poll-scheduler'

const SOURCES = KNOWLEDGE_COPY.sources
const PATH = '/bedrock-chat/dashboard/kb-sources'

const sources = [{ source: 'feedback', count: 4 }, { source: 'intel-docs', count: 12 }]

const running: KbSourceRun = {
  ...IDLE_RUN,
  runId: 'run-1',
  phase: 'running',
  sourceName: 'intel-docs',
  sourceType: 'web',
  pagesCrawled: 5,
  pagesProcessed: 3,
  chunksWritten: 8,
}

const duplicate: Problem = {
  code: 'http-error',
  title: 'Conflict',
  status: 409,
  serverCode: 'source_already_exists',
}

const createGateway = (overrides: Partial<KbSourcesGateway> = {}): KbSourcesGateway => ({
  listSources: jest.fn(async () => ok(sources)),
  status: jest.fn(async () => ok(IDLE_RUN)),
  startWebCrawl: jest.fn(async () => ok(running)),
  overrideWebCrawl: jest.fn(async () => ok<KbSourceRun>({ ...running, runId: 'run-2' })),
  startFileIngest: jest.fn(async () => ok<KbSourceRun>({ ...running, sourceType: 'file', sourceName: 'notes' })),
  overrideFileIngest: jest.fn(async () => ok<KbSourceRun>({ ...running, runId: 'run-2', sourceType: 'file' })),
  deleteSource: jest.fn(async () => ok({ source: 'feedback', deleted: 4 })),
  ...overrides,
})

const renderAt = (gateway = createGateway()) => {
  const base = fakeContainer()
  const notifications = new RecordingNotificationPort()
  const scheduler = new FakePollScheduler()
  const kbSources = new KbSourcesStore({
    gateway,
    confirmations: base.confirmations,
    notifications,
    scheduler,
    logger: base.logger,
  })
  const container: Container = { ...base, kbSourcesGateway: gateway, kbSources }
  const router = createAppRouter(container, { history: createMemoryHistory({ initialEntries: [PATH] }) })
  const view = render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
      <ConfirmationHost controller={container.confirmations} />
    </ContainerContext.Provider>,
  )

  return { gateway, notifications, scheduler, dom: view.container }
}

const webForm = async () => screen.findByRole('form', { name: SOURCES.web.title })
const fileForm = async () => screen.findByRole('form', { name: SOURCES.file.title })

const fillWebForm = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
  const form = await webForm()
  await user.type(within(form).getByRole('textbox', { name: SOURCES.web.name }), name)
  await user.type(within(form).getByRole('textbox', { name: SOURCES.web.urls }), 'https://a.example{enter}https://b.example')
  return form
}

describe('KB sources page', () => {
  it('lists ingested sources with their document counts and hides the run panel while idle', async () => {
    renderAt()

    expect(await screen.findByRole('heading', { level: 1, name: SHELL.admin.kbSources })).toBeInTheDocument()
    const row = (await screen.findByText('intel-docs')).closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('12')).toBeInTheDocument()
    expect(within(row as HTMLElement).getByRole('button', { name: SOURCES.list.deleteLabel('intel-docs') })).toBeInTheDocument()
    expect(screen.queryByText(SOURCES.run.title)).not.toBeInTheDocument()
  })

  it('shows a run already in progress and keeps both forms held while it runs', async () => {
    const gateway = createGateway({ status: jest.fn(async () => ok(running)) })
    renderAt(gateway)

    expect(await screen.findByText(SOURCES.run.heading(SOURCES.run.phase.running, 'intel-docs', SOURCES.run.type.web))).toBeInTheDocument()
    expect(screen.getByText(SOURCES.run.pagesCrawled).nextElementSibling).toHaveTextContent('5')
    expect(within(await webForm()).getByRole('button', { name: SOURCES.web.submit })).toBeDisabled()
    expect(within(await fileForm()).getByRole('button', { name: SOURCES.file.submit })).toBeDisabled()
  })

  it('has no accessibility violations', async () => {
    const failed: KbSourceRun = { ...running, errors: ['x'], error: 'boom', phase: 'failed' }
    const { dom } = renderAt(createGateway({ status: jest.fn(async () => ok(failed)) }))

    await screen.findByText('intel-docs')
    await screen.findByText(SOURCES.run.itemErrors(1))
    const results = await axe(dom)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})

describe('web crawl form', () => {
  it('blocks submission with field-level issues and never reaches the gateway', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    const form = await webForm()
    await user.click(within(form).getByRole('button', { name: SOURCES.web.submit }))

    expect(await within(form).findByText(SOURCES.issues['name-required'])).toBeInTheDocument()
    expect(within(form).getByText(SOURCES.issues['urls-required'])).toBeInTheDocument()
    expect(within(form).getByRole('textbox', { name: SOURCES.web.name })).toHaveAttribute('aria-invalid', 'true')
    expect(gateway.startWebCrawl).not.toHaveBeenCalled()
  })

  it('starts a crawl from the parsed draft, including the linked-PDF and synthesis opt-ins, and shows the run', async () => {
    const user = userEvent.setup()
    const { gateway, notifications } = renderAt()

    const form = await fillWebForm(user, 'intel-docs')
    await user.click(within(form).getByRole('checkbox', { name: SOURCES.web.ingestLinkedFiles }))
    await user.click(within(form).getByRole('checkbox', { name: SOURCES.web.synthesize }))
    await user.click(within(form).getByRole('button', { name: SOURCES.web.submit }))

    await waitFor(() => expect(gateway.startWebCrawl).toHaveBeenCalledTimes(1))
    expect(jest.mocked(gateway.startWebCrawl).mock.calls[0]?.[0]).toEqual({
      name: 'intel-docs',
      urls: ['https://a.example', 'https://b.example'],
      topic: null,
      maxDepth: 2,
      maxPages: 100,
      allowedDomains: null,
      excludePatterns: null,
      ingestLinkedFiles: true,
      synthesize: true,
      headers: null,
      cookies: null,
    })
    expect(notifications.messages()).toContain(SOURCES.web.started('run-1'))
    expect(await screen.findByText(SOURCES.run.heading(SOURCES.run.phase.running, 'intel-docs', SOURCES.run.type.web))).toBeInTheDocument()
  })

  it('reveals the header and cookie fields on demand and reports invalid JSON there', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    const form = await fillWebForm(user, 'intel-docs')
    await user.click(within(form).getByRole('button', { name: SOURCES.web.advanced }))
    await user.type(await within(form).findByRole('textbox', { name: SOURCES.web.headers }), '{{not json')
    await user.click(within(form).getByRole('button', { name: SOURCES.web.submit }))

    expect(await within(form).findByText(SOURCES.issues['invalid-json'])).toBeInTheDocument()
    expect(gateway.startWebCrawl).not.toHaveBeenCalled()
  })

  it('asks before overriding an existing source and re-submits as an override when confirmed', async () => {
    const user = userEvent.setup()
    const { gateway, notifications } = renderAt(createGateway({ startWebCrawl: jest.fn(async () => err(duplicate)) }))

    const form = await fillWebForm(user, 'intel-docs')
    await user.click(within(form).getByRole('button', { name: SOURCES.web.submit }))

    const dialog = await screen.findByRole('dialog', { name: SOURCES.override.title })
    expect(within(dialog).getByText(SOURCES.override.message('intel-docs'))).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: SOURCES.override.confirm }))

    await waitFor(() => expect(gateway.overrideWebCrawl).toHaveBeenCalledTimes(1))
    expect(notifications.messages()).toContain(SOURCES.web.overrideStarted('run-2'))
  })

  it('leaves the source alone when the override is declined', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt(createGateway({ startWebCrawl: jest.fn(async () => err(duplicate)) }))

    const form = await fillWebForm(user, 'intel-docs')
    await user.click(within(form).getByRole('button', { name: SOURCES.web.submit }))

    const dialog = await screen.findByRole('dialog', { name: SOURCES.override.title })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: SOURCES.override.title })).not.toBeInTheDocument())
    expect(gateway.overrideWebCrawl).not.toHaveBeenCalled()
    expect(within(form).getByRole('button', { name: SOURCES.web.submit })).toBeEnabled()
  })
})

describe('file ingestion form', () => {
  it('uploads the chosen files under the source name and clears the picker on success', async () => {
    const user = userEvent.setup()
    const { gateway, notifications } = renderAt()

    const form = await fileForm()
    await user.type(within(form).getByRole('textbox', { name: SOURCES.file.name }), 'notes')
    const picker = within(form).getByLabelText(SOURCES.file.files)
    await user.upload(picker, [
      new File(['hello'], 'notes.txt', { type: 'text/plain' }),
      new File(['%PDF'], 'guide.pdf', { type: 'application/pdf' }),
    ])
    await user.click(within(form).getByRole('checkbox', { name: SOURCES.file.synthesize }))
    await user.click(within(form).getByRole('button', { name: SOURCES.file.submit }))

    await waitFor(() => expect(gateway.startFileIngest).toHaveBeenCalledTimes(1))
    const request = jest.mocked(gateway.startFileIngest).mock.calls[0]?.[0]
    expect(request).toMatchObject({ name: 'notes', topic: null, synthesize: true })
    expect(request?.files.map((file) => file.name)).toEqual(['notes.txt', 'guide.pdf'])
    expect(notifications.messages()).toContain(SOURCES.file.started('run-1'))
    await waitFor(() => expect((within(form).getByLabelText(SOURCES.file.files) as HTMLInputElement).files).toHaveLength(0))
  })

  it('requires at least one file', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    const form = await fileForm()
    await user.type(within(form).getByRole('textbox', { name: SOURCES.file.name }), 'notes')
    await user.click(within(form).getByRole('button', { name: SOURCES.file.submit }))

    expect(await within(form).findByText(SOURCES.issues['files-required'])).toBeInTheDocument()
    expect(gateway.startFileIngest).not.toHaveBeenCalled()
  })
})

describe('deleting a source', () => {
  it('confirms, deletes, announces the count and refreshes the list', async () => {
    const user = userEvent.setup()
    const { gateway, notifications } = renderAt()

    await user.click(await screen.findByRole('button', { name: SOURCES.list.deleteLabel('feedback') }))

    const dialog = await screen.findByRole('dialog', { name: SOURCES.list.deleteConfirmTitle })
    expect(within(dialog).getByText(SOURCES.list.deleteConfirmMessage('feedback', 4))).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: SOURCES.list.deleteConfirmLabel }))

    await waitFor(() => expect(gateway.deleteSource).toHaveBeenCalledWith('feedback'))
    expect(notifications.messages()).toContain(SOURCES.list.deleteSuccess('feedback', 4))
    await waitFor(() => expect(gateway.listSources).toHaveBeenCalledTimes(2))
  })
})
