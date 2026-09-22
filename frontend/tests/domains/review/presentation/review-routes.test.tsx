import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { createAppRouter } from '@/app/router'
import { ReviewStore } from '@/domains/review/application/review.store'
import type { Page, ReviewGateway, ReviewQuery } from '@/domains/review/application/ports'
import type { FeedbackEntrySummary } from '@/domains/review/domain/public'
import { ok } from '@/shared/kernel/result'
import { REVIEW_COPY } from '@/shared/copy/review'

import { fakeContainer } from '../../../app/bootstrap/container.fixture'
import { ScriptedConfirmationPort } from '../../../shared/ports/scripted-confirmation-port'
import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'
import { anEntry, aSummary } from '../domain/feedback-entry.fixture'

const rejected = aSummary('rejected-id', 'rejected', { query: 'Rejected answer' })
const approved = aSummary('approved-id', 'approved', { query: 'Approved answer' })

const response: Page<FeedbackEntrySummary> = {
  items: [rejected, approved],
  total: 2,
  limit: 50,
  offset: 0,
}

const renderAt = (path: string) => {
  const list = vi.fn().mockResolvedValue(ok(response))
  const gateway = {
    list,
    get: vi.fn().mockResolvedValue(ok(anEntry({ id: rejected.id, reviewStatus: 'rejected' }))),
    saveDecision: vi.fn(),
    remove: vi.fn().mockResolvedValue(ok(undefined)),
    stats: vi.fn(),
    pendingCount: vi.fn().mockResolvedValue(ok(0)),
    synthesize: vi.fn(),
    rollback: vi.fn(),
    synthesisPhase: vi.fn().mockResolvedValue(ok('idle')),
  } as ReviewGateway
  const base = fakeContainer()
  const reviews = new ReviewStore({
    gateway,
    confirmations: new ScriptedConfirmationPort([true]),
    notifications: new RecordingNotificationPort(),
    logger: base.logger,
  })
  const container = { ...base, reviewGateway: gateway, reviews }
  const router = createAppRouter(container, {
    history: createMemoryHistory({ initialEntries: [path] }),
  })

  const { container: dom } = render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
    </ContainerContext.Provider>,
  )

  return { router, list, dom }
}

const lastQuery = (list: ReturnType<typeof vi.fn>): ReviewQuery | undefined =>
  list.mock.calls.at(-1)?.[0] as ReviewQuery | undefined

const routeSearch = (router: ReturnType<typeof createAppRouter>): Record<string, unknown> =>
  router.state.matches.at(-1)?.search ?? {}

describe('review queue URL binding', () => {
  it('restores every list filter and offset from a shareable URL', async () => {
    const { list } = renderAt(
      '/bedrock-chat/dashboard/feedback?rating=negative&tags=emon%2Cipc&from=2026-09-01&to=2026-09-08&offset=50',
    )

    await waitFor(() => expect(list).toHaveBeenCalled())
    expect(lastQuery(list)).toMatchObject({
      status: 'pending_review',
      rating: 'negative',
      tags: ['emon', 'ipc'],
      offset: 50,
    })
    expect(lastQuery(list)?.dateFrom?.toIso()).toBe('2026-09-01')
    expect(lastQuery(list)?.dateTo?.toIso()).toBe('2026-09-08')
  })

  it('applies a rating immediately and resets the offset', async () => {
    const user = userEvent.setup()
    const { router } = renderAt('/bedrock-chat/dashboard/feedback?rating=negative&offset=50')

    await user.selectOptions(await screen.findByRole('combobox', { name: REVIEW_COPY.filters.rating }), 'positive')

    await waitFor(() => expect(routeSearch(router)).toMatchObject({ rating: 'positive', offset: 0 }))
  })

  it('debounces a text filter into the URL and resets the offset', async () => {
    const user = userEvent.setup()
    const { router } = renderAt('/bedrock-chat/dashboard/feedback?offset=50')

    await user.type(await screen.findByRole('textbox', { name: REVIEW_COPY.filters.tags }), 'emon')

    await waitFor(() => expect(routeSearch(router)).toMatchObject({ tags: 'emon', offset: 0 }))
  })

  it('opens a row as a URL-addressable, focus-trapped drawer', async () => {
    const user = userEvent.setup()
    const { router } = renderAt('/bedrock-chat/dashboard/feedback')

    await user.click(await screen.findByRole('button', { name: /Open Rejected answer/ }))

    expect(await screen.findByRole('dialog', { name: REVIEW_COPY.drawer.title })).toBeInTheDocument()
    expect(routeSearch(router)).toMatchObject({ entry: 'rejected-id' })
  })

  it('constrains query previews and top-aligns row metadata', async () => {
    renderAt('/bedrock-chat/dashboard/feedback')

    const query = await screen.findByText('Rejected answer')

    expect(query).toHaveClass('line-clamp-3', '[overflow-wrap:anywhere]')
    expect(query.closest('td')).toHaveClass('max-w-[50vw]', 'align-top')
    expect(query.closest('tr')?.querySelectorAll('td.align-top')).toHaveLength(6)
  })
})

describe('reviewed selection', () => {
  it('offers selection only for rejected entries', async () => {
    renderAt('/bedrock-chat/dashboard/reviewed?decision=all')

    expect(await screen.findByRole('checkbox', { name: REVIEW_COPY.table.selectEntry('Rejected answer') })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: REVIEW_COPY.table.selectEntry('Approved answer') })).not.toBeInTheDocument()
  })
})

describe('review route accessibility', () => {
  it.each([
    ['/bedrock-chat/dashboard/feedback', 'queue'],
    ['/bedrock-chat/dashboard/reviewed?decision=all', 'reviewed'],
  ])('%s has no axe violations', async (path) => {
    const { dom } = renderAt(path)

    await screen.findByRole('heading', { level: 1 })
    await screen.findByText('Rejected answer')
    const results = await axe(dom)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})