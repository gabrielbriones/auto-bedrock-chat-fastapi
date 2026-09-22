import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { describe, expect, it, vi } from 'vitest'
import { axe } from 'vitest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { createAppRouter } from '@/app/router'
import type { ByUserUsageQuery, TelemetryGateway } from '@/domains/telemetry/application/ports'
import { TelemetryStore } from '@/domains/telemetry/application/telemetry.store'
import {
  createTokenCount,
  type DailyUsageRow,
  type ModelUsageRow,
  type SessionUsageRow,
  type UserUsageRow,
} from '@/domains/telemetry/domain/public'
import { formatUsageNumber } from '@/domains/telemetry/presentation/format-usage'
import { TELEMETRY_COPY } from '@/shared/copy/telemetry'
import { CalendarDate, Instant } from '@/shared/kernel/instant'
import type { Problem } from '@/shared/http/exception'
import { err, isOk, ok } from '@/shared/kernel/result'

import { fakeContainer } from '../../../app/bootstrap/container.fixture'

const date = (value: string): CalendarDate => {
  const result = CalendarDate.fromIso(value)
  if (!isOk(result)) throw new Error(`invalid test date: ${value}`)
  return result.value
}

const instant = (value: string): Instant => {
  const result = Instant.fromIso(value)
  if (!isOk(result)) throw new Error(`invalid test instant: ${value}`)
  return result.value
}

const summaryRows: readonly ModelUsageRow[] = [
  { modelId: 'claude-3', tokens: createTokenCount(120, 30), turnCount: 4 },
]

const topUserRows: readonly UserUsageRow[] = [
  { userId: 'alice@example.com', tokens: createTokenCount(100, 50) },
]

const dailyRows: readonly DailyUsageRow[] = [
  { date: date('2026-05-01'), tokens: createTokenCount(12, 3), turnCount: 2 },
  { date: date('2026-05-02'), tokens: createTokenCount(20, 8), turnCount: 3 },
]

const sessionRow: SessionUsageRow = {
  sessionId: 'session-1234567890123456789012345678901234567890',
  modelId: 'claude-3',
  tokens: createTokenCount(2, 3),
  turnAt: instant('2026-05-01T12:00:00.000Z'),
}

const problem = (overrides: Partial<Problem> = {}): Problem => ({
  code: 'http-error',
  title: 'Request failed',
  ...overrides,
})

const createGateway = (overrides: Partial<TelemetryGateway> = {}): TelemetryGateway => ({
  summary: vi.fn(async () => ok(summaryRows)),
  topUsers: vi.fn(async () => ok(topUserRows)),
  byDay: vi.fn(async () => ok(dailyRows)),
  byUser: vi.fn(async () => ok([sessionRow])),
  ...overrides,
})

const renderAt = (path: string, gateway = createGateway()) => {
  const container = fakeContainer({
    telemetryGateway: gateway,
    telemetry: new TelemetryStore({ gateway }),
  })
  const router = createAppRouter(container, {
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const view = render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
    </ContainerContext.Provider>,
  )

  return { router, gateway, dom: view.container }
}

const cardFor = async (title: string): Promise<HTMLElement> => {
  const titleNode = await screen.findByText(title, { exact: true })
  const card = titleNode.closest('[data-slot="card"]')
  if (!(card instanceof HTMLElement)) throw new Error(`card not found: ${title}`)
  return card
}

const routeSearch = (router: ReturnType<typeof createAppRouter>): Record<string, unknown> =>
  router.state.matches.at(-1)?.search ?? {}

const lastByUserQuery = (gateway: TelemetryGateway): ByUserUsageQuery | undefined =>
  vi.mocked(gateway.byUser).mock.calls.at(-1)?.[0] as ByUserUsageQuery | undefined

describe('usage analytics route', () => {
  it('restores applied filters from the URL and fetches each selected section', async () => {
    const { gateway, router } = renderAt(
      '/bedrock-chat/dashboard/token-usages?topLimit=20&from=2026-05-01&to=2026-05-31&user=alice%40example.com&offset=50',
    )

    expect(await screen.findByLabelText(TELEMETRY_COPY.byDay.start)).toHaveValue('2026-05-01')
    expect(screen.getByLabelText(TELEMETRY_COPY.byDay.end)).toHaveValue('2026-05-31')
    expect(screen.getByLabelText(TELEMETRY_COPY.byUser.user)).toHaveValue('alice@example.com')

    await waitFor(() => {
      expect(gateway.summary).toHaveBeenCalledOnce()
      expect(gateway.topUsers).toHaveBeenCalledWith(20, expect.any(AbortSignal))
      expect(gateway.byDay).toHaveBeenCalledOnce()
      expect(gateway.byUser).toHaveBeenCalledOnce()
    })

    const byDayRange = vi.mocked(gateway.byDay).mock.calls[0]?.[0]
    expect(byDayRange?.start.toIso()).toBe('2026-05-01')
    expect(byDayRange?.end.toIso()).toBe('2026-05-31')
    expect(lastByUserQuery(gateway)).toMatchObject({
      userId: 'alice@example.com',
      page: { offset: 50, limit: 50 },
    })
    expect(routeSearch(router)).toMatchObject({
      topLimit: 20,
      from: '2026-05-01',
      to: '2026-05-31',
      user: 'alice@example.com',
      offset: 50,
    })
  })

  it('keeps local drafts unapplied until Apply and supports Enter for users', async () => {
    const user = userEvent.setup()
    const { gateway, router } = renderAt('/bedrock-chat/dashboard/token-usages')
    const byDayCard = await cardFor(TELEMETRY_COPY.byDay.title)
    const byUserCard = await cardFor(TELEMETRY_COPY.byUser.title)

    expect(gateway.byDay).not.toHaveBeenCalled()
    expect(gateway.byUser).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText(TELEMETRY_COPY.byDay.start), { target: { value: '2026-05-01' } })
    fireEvent.change(screen.getByLabelText(TELEMETRY_COPY.byDay.end), { target: { value: '2026-05-31' } })
    expect(gateway.byDay).not.toHaveBeenCalled()

    await user.click(within(byDayCard).getByRole('button', { name: TELEMETRY_COPY.byDay.apply }))
    await waitFor(() => expect(routeSearch(router)).toMatchObject({ from: '2026-05-01', to: '2026-05-31' }))
    await waitFor(() => expect(gateway.byDay).toHaveBeenCalledOnce())

    const userInput = within(byUserCard).getByRole('searchbox')
    await user.type(userInput, 'alice@example.com')
    await user.keyboard('{Enter}')

    await waitFor(() => {
      expect(routeSearch(router)).toMatchObject({ user: 'alice@example.com', offset: 0 })
      expect(gateway.byUser).toHaveBeenCalledOnce()
    })
  })

  it('refuses an inverted date range without calling the gateway', async () => {
    const user = userEvent.setup()
    const { gateway, router } = renderAt('/bedrock-chat/dashboard/token-usages')
    const byDayCard = await cardFor(TELEMETRY_COPY.byDay.title)

    fireEvent.change(screen.getByLabelText(TELEMETRY_COPY.byDay.start), { target: { value: '2026-05-31' } })
    fireEvent.change(screen.getByLabelText(TELEMETRY_COPY.byDay.end), { target: { value: '2026-05-01' } })
    await user.click(within(byDayCard).getByRole('button', { name: TELEMETRY_COPY.byDay.apply }))

    expect(await screen.findByRole('alert')).toHaveTextContent(TELEMETRY_COPY.byDay.invalidRange)
    expect(gateway.byDay).not.toHaveBeenCalled()
    expect(routeSearch(router)).not.toMatchObject({ from: '2026-05-31', to: '2026-05-01' })
  })

  it('keeps section errors independent while other sections render data', async () => {
    const gateway = createGateway({
      summary: vi.fn(async () => err(problem())),
      byDay: vi.fn(async () => err(problem())),
      byUser: vi.fn(async () => err(problem())),
    })

    renderAt('/bedrock-chat/dashboard/token-usages?from=2026-05-01&to=2026-05-31&user=alice', gateway)

    expect(await screen.findByText(TELEMETRY_COPY.summary.loadError)).toBeInTheDocument()
    expect(await screen.findByText(TELEMETRY_COPY.byDay.loadError)).toBeInTheDocument()
    expect(await screen.findByText(TELEMETRY_COPY.byUser.loadError)).toBeInTheDocument()
    expect(screen.getByText('alice@example.com')).toBeInTheDocument()
  })

  it('refetches only Top Users when its URL-owned limit changes', async () => {
    const user = userEvent.setup()
    const { gateway, router } = renderAt('/bedrock-chat/dashboard/token-usages')
    const topUsersCard = await cardFor(TELEMETRY_COPY.topUsers.title)

    await waitFor(() => expect(gateway.topUsers).toHaveBeenCalledWith(10, expect.any(AbortSignal)))
    await user.click(within(topUsersCard).getByRole('combobox', { name: TELEMETRY_COPY.topUsers.limit }))
    await user.click(await screen.findByRole('option', { name: '20' }))

    await waitFor(() => expect(vi.mocked(gateway.topUsers).mock.calls.at(-1)?.[0]).toBe(20))
    expect(vi.mocked(gateway.summary)).toHaveBeenCalledOnce()
    expect(gateway.byDay).not.toHaveBeenCalled()
    expect(gateway.byUser).not.toHaveBeenCalled()
    expect(routeSearch(router)).toMatchObject({ topLimit: 20 })
  })

  it('renders cursorless next-page navigation without a total', async () => {
    const user = userEvent.setup()
    const firstPage = Array.from({ length: 50 }, (_, index) => ({
      ...sessionRow,
      sessionId: `session-${index}`,
    }))
    const gateway = createGateway({
      byUser: vi.fn(async (query: ByUserUsageQuery) =>
        ok(query.page.offset === 0 ? firstPage : [sessionRow])),
    })
    const { router } = renderAt('/bedrock-chat/dashboard/token-usages?user=alice', gateway)

    const next = await screen.findByRole('button', { name: TELEMETRY_COPY.pagination.next })
    expect(next).not.toBeDisabled()
    await user.click(next)

    await waitFor(() => expect(routeSearch(router)).toMatchObject({ user: 'alice', offset: 50 }))
    expect(await screen.findByText(TELEMETRY_COPY.pagination.range(51, 51))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: TELEMETRY_COPY.pagination.previous })).not.toBeDisabled()
    expect(screen.getByRole('button', { name: TELEMETRY_COPY.pagination.next })).toBeDisabled()
    expect(lastByUserQuery(gateway)?.page.offset).toBe(50)
  })

  it('keeps empty states exact and exposes chart data through tables', async () => {
    const gateway = createGateway({
      byUser: vi.fn(async () => ok([])),
    })
    const { dom } = renderAt('/bedrock-chat/dashboard/token-usages?from=2026-05-01&to=2026-05-31&user=alice', gateway)

    expect(await screen.findByText(TELEMETRY_COPY.byUser.empty)).toBeInTheDocument()
    const summaryTable = await screen.findByRole('table', { name: TELEMETRY_COPY.summary.table })
    const byDayTable = await screen.findByRole('table', { name: TELEMETRY_COPY.byDay.table })
    expect(within(summaryTable).getByText(formatUsageNumber(summaryRows[0]!.tokens.input))).toBeInTheDocument()
    expect(within(byDayTable).getByText(formatUsageNumber(dailyRows[0]!.tokens.input))).toBeInTheDocument()
    expect(screen.getByText(TELEMETRY_COPY.summary.chartTableDescription)).toBeInTheDocument()
    expect(screen.getByText(TELEMETRY_COPY.byDay.chartTableDescription)).toBeInTheDocument()
    expect(dom.querySelector('[aria-labelledby="usage-model-chart-title"] [aria-hidden="true"]')).not.toBeNull()
    expect(dom.querySelector('[aria-labelledby="usage-daily-chart-title"] [aria-hidden="true"]')).not.toBeNull()

    const results = await axe(dom)
    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
