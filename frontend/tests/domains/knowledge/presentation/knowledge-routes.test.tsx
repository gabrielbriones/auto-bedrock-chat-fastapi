import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { describe, expect, it, jest } from '@jest/globals'
import { axe } from 'jest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { createAppRouter } from '@/app/router'
import { ConfirmationHost } from '@/app/providers/ConfirmationHost'
import type { Container } from '@/app/bootstrap/container'
import type { KnowledgeGateway, Page, KbQuery } from '@/domains/knowledge/application/ports'
import { KnowledgeStore } from '@/domains/knowledge/application/knowledge.store'
import type { KbDocument, KbDocumentSummary } from '@/domains/knowledge/domain/public'
import { createCredibility } from '@/domains/knowledge/domain/public'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import { kbDocumentId } from '@/shared/kernel/branded'
import { Instant } from '@/shared/kernel/instant'
import type { RollbackResult } from '@/shared/kernel/curation'
import { ok } from '@/shared/kernel/result'

import { fakeContainer } from '../../../app/bootstrap/container.fixture'
import { ScriptedConfirmationPort, type ScriptedAnswer } from '../../../shared/ports/scripted-confirmation-port'
import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'

const documentSummary = (
  id: string,
  overrides: Partial<KbDocumentSummary> = {},
): KbDocumentSummary => ({
  id: kbDocumentId(id),
  title: id,
  source: 'ISS docs',
  sourceUrl: null,
  topic: 'compute',
  tags: ['ipc', 'vector'],
  chunkCount: 2,
  createdAt: null,
  credibility: createCredibility(0.8, false),
  ...overrides,
})

const response: Page<KbDocumentSummary> = {
  items: [
    documentSummary('Healthy guide'),
    documentSummary('Removal candidate', { credibility: createCredibility(0.2, true) }),
    documentSummary('Needs review', {
      title: null,
      source: null,
      topic: null,
      tags: ['a', 'b', 'c', 'd', 'e', 'f'],
      credibility: createCredibility(0.5, false),
    }),
  ],
  total: 52,
  limit: 50,
  offset: 0,
}

const anOpenedDocument = (overrides: Partial<KbDocument> = {}): KbDocument => ({
  id: kbDocumentId('Healthy guide'),
  title: 'Healthy guide',
  source: 'ISS docs',
  sourceUrl: null,
  topic: 'compute',
  tags: ['ipc'],
  content: 'Original content',
  datePublished: null,
  metadata: {},
  chunkCount: 2,
  createdAt: null,
  credibility: createCredibility(0.8, false),
  ...overrides,
})

const renderAt = (
  path: string,
  options: {
    readonly page?: Page<KbDocumentSummary>
    readonly gateway?: Partial<KnowledgeGateway>
    readonly confirmAnswers?: readonly ScriptedAnswer[]
  } = {},
) => {
  const list = jest.fn().mockResolvedValue(ok(options.page ?? response))
  const gateway = {
    list,
    get: jest.fn().mockResolvedValue(ok(anOpenedDocument())),
    patch: jest.fn().mockResolvedValue(ok(anOpenedDocument())),
    remove: jest.fn().mockResolvedValue(ok(undefined)),
    resetCredibility: jest.fn().mockResolvedValue(ok(anOpenedDocument())),
    rollback: jest
      .fn()
      .mockResolvedValue(ok({ kbDocumentId: anOpenedDocument().id, rolledBackAt: Instant.EPOCH } satisfies RollbackResult)),
    ...options.gateway,
  } as KnowledgeGateway
  const base = fakeContainer()
  const confirmations = new ScriptedConfirmationPort(options.confirmAnswers ?? [])
  const notifications = new RecordingNotificationPort()
  const knowledge = new KnowledgeStore({ gateway, confirmations, notifications, logger: base.logger })
  const container: Container = { ...base, knowledgeGateway: gateway, knowledge }
  const router = createAppRouter(container, {
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const { container: dom } = render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
      <ConfirmationHost controller={container.confirmations} />
    </ContainerContext.Provider>,
  )

  return { router, list, dom, gateway, confirmations, notifications }
}

const lastQuery = (list: ReturnType<typeof jest.fn>): KbQuery | undefined =>
  list.mock.calls.at(-1)?.[0] as KbQuery | undefined

const routeSearch = (router: ReturnType<typeof createAppRouter>): Record<string, unknown> =>
  router.state.matches.at(-1)?.search ?? {}

describe('knowledge browser URL binding', () => {
  it('restores filters and offset from a shareable URL', async () => {
    const { list } = renderAt(
      '/bedrock-chat/dashboard/kb-browser?source=ISS%20docs&topic=memory%2Fcache&tags=ipc%2Cperf&from=2026-09-01&to=2026-09-08&flagged=true&offset=50',
      { page: { ...response, offset: 50 } },
    )

    await waitFor(() => expect(list).toHaveBeenCalled())
    expect(lastQuery(list)).toMatchObject({
      source: 'ISS docs',
      topic: 'memory/cache',
      tags: ['ipc', 'perf'],
      removalFlagged: true,
      offset: 50,
    })
    expect(lastQuery(list)?.dateFrom?.toIso()).toBe('2026-09-01')
    expect(lastQuery(list)?.dateTo?.toIso()).toBe('2026-09-08')
  })

  it('updates the URL when a filter changes and keeps the explicit offset reset', async () => {
    const user = userEvent.setup()
    const { router } = renderAt('/bedrock-chat/dashboard/kb-browser?offset=50')

    await user.type(await screen.findByRole('searchbox', { name: KNOWLEDGE_COPY.filters.source }), 'ISS')

    await waitFor(() => expect(routeSearch(router)).toMatchObject({ source: 'ISS', offset: 0 }))
  })

  it('restores filter controls and resets the URL-bound state', async () => {
    const user = userEvent.setup()
    const { router } = renderAt(
      '/bedrock-chat/dashboard/kb-browser?source=ISS%20docs&topic=compute&tags=ipc&from=2026-09-01&to=2026-09-08&flagged=true&offset=50',
      { page: { ...response, offset: 50 } },
    )

    expect(await screen.findByDisplayValue('ISS docs')).toBeInTheDocument()
    expect(screen.getByDisplayValue('compute')).toBeInTheDocument()
    expect(screen.getByDisplayValue('ipc')).toBeInTheDocument()
    expect(screen.getByDisplayValue('2026-09-01')).toBeInTheDocument()
    expect(screen.getByDisplayValue('2026-09-08')).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).toHaveAttribute('aria-checked', 'true')

    await user.click(screen.getByRole('button', { name: 'Reset' }))

    await waitFor(() => expect(routeSearch(router)).toMatchObject({ flagged: false, offset: 0 }))
    expect(routeSearch(router)).not.toHaveProperty('source')
  })
})

describe('knowledge browser status cues', () => {
  it('labels flagged rows and credibility bands without colour as the only cue', async () => {
    renderAt('/bedrock-chat/dashboard/kb-browser')

    const flaggedRow = await screen.findByText('Removal candidate')
    const row = flaggedRow.closest('tr')

    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText(KNOWLEDGE_COPY.table.flagged)).toBeInTheDocument()
    expect(within(row as HTMLElement).getByText(/Low credibility \(20%\)/)).toBeInTheDocument()
    expect(screen.getByText(/High credibility \(80%\)/)).toBeInTheDocument()
    expect(screen.getByText(KNOWLEDGE_COPY.table.untitled)).toBeInTheDocument()
    expect(screen.getAllByText(KNOWLEDGE_COPY.table.notFlagged)).toHaveLength(2)
    expect(screen.getByText('+1')).toBeInTheDocument()
    expect(screen.getByText(/Needs review \(50%\)/)).toBeInTheDocument()
  })

  it('has no accessibility violations', async () => {
    const { dom } = renderAt('/bedrock-chat/dashboard/kb-browser')

    await screen.findByText('Healthy guide')
    const results = await axe(dom)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})

describe('knowledge editor drawer', () => {
  const openRow = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(await screen.findByRole('button', { name: 'Open Healthy guide' }))
    return screen.findByRole('dialog', { name: KNOWLEDGE_COPY.drawer.title })
  }

  it('opens a row as a URL-addressable drawer, and keeps the editor field independent of the filter field (FIX-02)', async () => {
    const user = userEvent.setup()
    const { router } = renderAt('/bedrock-chat/dashboard/kb-browser')

    const dialog = await openRow(user)

    expect(routeSearch(router)).toMatchObject({ doc: 'Healthy guide' })
    expect(within(dialog).getByRole('textbox', { name: KNOWLEDGE_COPY.editor.title })).toHaveValue('Healthy guide')

    // The filter row sits outside the drawer, which Base UI marks inert while open — `hidden:
    // true` reaches past that to prove the two "Topic" fields are genuinely separate elements/ids.
    const topicTextboxes = screen.getAllByRole('textbox', { name: KNOWLEDGE_COPY.filters.topic, hidden: true })
    const filterTopic = topicTextboxes.find((element) => !dialog.contains(element))
    const editorTopic = within(dialog).getByRole('textbox', { name: KNOWLEDGE_COPY.editor.topic })
    if (filterTopic === undefined) {
      throw new Error('expected a filter Topic field outside the drawer')
    }

    expect(filterTopic).not.toBe(editorTopic)
    expect(filterTopic.id).not.toBe(editorTopic.id)

    fireEvent.change(filterTopic, { target: { value: 'filter-only' } })
    expect(editorTopic).toHaveValue('compute')

    fireEvent.change(editorTopic, { target: { value: 'editor-only' } })
    expect(filterTopic).toHaveValue('filter-only')
  })

  it('warns before saving a content change and lets the user cancel', async () => {
    const user = userEvent.setup()
    const patch = jest.fn().mockResolvedValue(ok(anOpenedDocument()))
    renderAt('/bedrock-chat/dashboard/kb-browser', { gateway: { patch }, confirmAnswers: [false] })

    const dialog = await openRow(user)
    const content = within(dialog).getByRole('textbox', { name: KNOWLEDGE_COPY.editor.content })
    await user.clear(content)
    await user.type(content, 'Updated content body')
    await user.click(within(dialog).getByRole('button', { name: KNOWLEDGE_COPY.editor.save }))

    expect(patch).not.toHaveBeenCalled()
  })

  it('saves a content change once the re-embed warning is confirmed', async () => {
    const user = userEvent.setup()
    const patch = jest.fn().mockResolvedValue(ok(anOpenedDocument({ content: 'Updated content body' })))
    renderAt('/bedrock-chat/dashboard/kb-browser', { gateway: { patch }, confirmAnswers: [true] })

    const dialog = await openRow(user)
    const content = within(dialog).getByRole('textbox', { name: KNOWLEDGE_COPY.editor.content })
    await user.clear(content)
    await user.type(content, 'Updated content body')
    await user.click(within(dialog).getByRole('button', { name: KNOWLEDGE_COPY.editor.save }))

    await waitFor(() => expect(patch).toHaveBeenCalledWith(anOpenedDocument().id, { content: 'Updated content body' }))
  })

  it('blocks saving invalid metadata JSON without calling the gateway', async () => {
    const user = userEvent.setup()
    const patch = jest.fn()
    renderAt('/bedrock-chat/dashboard/kb-browser', { gateway: { patch } })

    const dialog = await openRow(user)
    const metadata = within(dialog).getByRole('textbox', { name: KNOWLEDGE_COPY.editor.metadata })
    await user.clear(metadata)
    await user.type(metadata, 'not json')
    await user.click(within(dialog).getByRole('button', { name: KNOWLEDGE_COPY.editor.save }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(KNOWLEDGE_COPY.editor.metadataInvalid)
    expect(patch).not.toHaveBeenCalled()
  })

  it('prompts before discarding unsaved edits, and keeps the drawer open when declined', async () => {
    const user = userEvent.setup()
    const { router } = renderAt('/bedrock-chat/dashboard/kb-browser')

    const dialog = await openRow(user)
    const title = within(dialog).getByRole('textbox', { name: KNOWLEDGE_COPY.editor.title })
    await user.type(title, ' v2')

    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    const confirmDialog = await screen.findByRole('dialog', { name: KNOWLEDGE_COPY.editor.discardTitle })
    await user.click(within(confirmDialog).getByRole('button', { name: 'Cancel' }))

    expect(await screen.findByRole('dialog', { name: KNOWLEDGE_COPY.drawer.title })).toBeInTheDocument()
    expect(routeSearch(router)).toMatchObject({ doc: 'Healthy guide' })
  })

  it('discards unsaved edits and closes the drawer when confirmed', async () => {
    const user = userEvent.setup()
    const { router } = renderAt('/bedrock-chat/dashboard/kb-browser')

    const dialog = await openRow(user)
    const title = within(dialog).getByRole('textbox', { name: KNOWLEDGE_COPY.editor.title })
    await user.type(title, ' v2')

    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    const confirmDialog = await screen.findByRole('dialog', { name: KNOWLEDGE_COPY.editor.discardTitle })
    await user.click(within(confirmDialog).getByRole('button', { name: KNOWLEDGE_COPY.editor.discardConfirmLabel }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: KNOWLEDGE_COPY.drawer.title })).not.toBeInTheDocument())
    expect(routeSearch(router)).not.toHaveProperty('doc')
  })

  it('restores the credibility score without a confirmation prompt', async () => {
    const user = userEvent.setup()
    const resetCredibility = jest.fn().mockResolvedValue(ok(anOpenedDocument({ credibility: createCredibility(1, false) })))
    const { confirmations, notifications } = renderAt('/bedrock-chat/dashboard/kb-browser', { gateway: { resetCredibility } })

    const dialog = await openRow(user)
    await user.click(within(dialog).getByRole('button', { name: KNOWLEDGE_COPY.editor.restoreScore }))

    await waitFor(() => expect(resetCredibility).toHaveBeenCalledWith(anOpenedDocument().id))
    expect(notifications.messages()).toContain(KNOWLEDGE_COPY.editor.resetCredibilitySuccess)
    expect(confirmations.asked).toHaveLength(0)
  })

  it('rolls back the document after confirmation and closes the drawer', async () => {
    const user = userEvent.setup()
    const rollback = jest
      .fn()
      .mockResolvedValue(ok({ kbDocumentId: anOpenedDocument().id, rolledBackAt: Instant.EPOCH } satisfies RollbackResult))
    const { router, notifications } = renderAt('/bedrock-chat/dashboard/kb-browser', { gateway: { rollback }, confirmAnswers: [true] })

    const dialog = await openRow(user)
    await user.click(within(dialog).getByRole('button', { name: KNOWLEDGE_COPY.editor.rollback }))

    await waitFor(() => expect(rollback).toHaveBeenCalledWith(anOpenedDocument().id, null))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: KNOWLEDGE_COPY.drawer.title })).not.toBeInTheDocument())
    expect(notifications.messages()).toContain(KNOWLEDGE_COPY.editor.rollbackSuccess)
    expect(routeSearch(router)).not.toHaveProperty('doc')
  })

  it('deletes the document after confirmation, closes the drawer, and clears the doc param', async () => {
    const user = userEvent.setup()
    const remove = jest.fn().mockResolvedValue(ok(undefined))
    const { router, notifications } = renderAt('/bedrock-chat/dashboard/kb-browser', { gateway: { remove }, confirmAnswers: [true] })

    const dialog = await openRow(user)
    await user.click(within(dialog).getByRole('button', { name: KNOWLEDGE_COPY.editor.delete }))

    await waitFor(() => expect(remove).toHaveBeenCalledWith(anOpenedDocument().id))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: KNOWLEDGE_COPY.drawer.title })).not.toBeInTheDocument())
    expect(notifications.messages()).toContain(KNOWLEDGE_COPY.editor.deleteSuccess)
    expect(routeSearch(router)).not.toHaveProperty('doc')
  })
})