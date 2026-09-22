import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'

import { fakeContainer } from './bootstrap/container.fixture'
import { ContainerContext } from '@/app/bootstrap/container-context'
import type { Container } from '@/app/bootstrap/container'
import { createAppRouter } from '@/app/router'
import { CapabilityHttpProbe } from '@/domains/iam/infrastructure/capability-http.probe'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'
import { IAM_COPY } from '@/shared/copy/iam'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import { ok } from '@/shared/kernel/result'
import { SHELL } from '@/shared/copy/shell'

const findComposer = () =>
  screen.findByRole('textbox', { name: MESSAGING_COPY.composer.label })

const renderAt = (path: string, overrides: Partial<Container> = {}) => {
  const container = fakeContainer(overrides)
  const router = createAppRouter(container, {
    history: createMemoryHistory({ initialEntries: [path] }),
  })

  const { container: dom } = render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
    </ContainerContext.Provider>,
  )

  return { router, dom }
}

describe('navigating the route tree', () => {
  it('renders the chat route at the base path', async () => {
    renderAt('/bedrock-chat/ui')

    expect(await findComposer()).toBeInTheDocument()
  })

  // FR-CONV-011: the same chat view, addressed by conversation id.
  it('renders a conversation from its path parameter', async () => {
    const { router } = renderAt('/bedrock-chat/ui/c/abc-123')

    expect(await findComposer()).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/ui/c/abc-123')
  })

  // FR-SHELL-014a: a stale or hand-edited link must degrade, not crash.
  it('renders the page for a URL carrying a malformed and an unknown parameter', async () => {
    const { router } = renderAt('/bedrock-chat/dashboard/kb-browser?flagged=maybe&offset=-4&rogue=1')

    expect(await screen.findByRole('heading', { name: 'Knowledge base' })).toBeInTheDocument()

    // Both malformed values fall back; the unknown key is absent from the route's typed search
    // (see the schema tests) even though the router keeps it in the URL for other consumers.
    const search = router.state.matches.at(-1)?.search
    expect(search).toMatchObject({ flagged: false, offset: 0 })
  })

  it('sends the bare admin path to the review queue', async () => {
    const { router } = renderAt('/bedrock-chat/dashboard')

    expect(await screen.findByRole('heading', { name: 'Feedback queue' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/dashboard/feedback')
  })

  it('renders a not-found state with a way back for an unknown address', async () => {
    renderAt('/bedrock-chat/ui/nope')

    expect(await screen.findByText(SHELL.notFound.title)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: SHELL.notFound.action })).toBeInTheDocument()
  })
})

describe('admin capability guard', () => {
  it('renders access denied without rendering the admin subtree for a non-admin', async () => {
    renderAt('/bedrock-chat/dashboard/feedback', {
      capabilityProbe: {
        probe: async () => ({ isAdmin: false, isAnonymousAdmin: false, tokenUsageEnabled: false }),
        invalidate: () => {},
      },
    })

    expect(await screen.findByRole('heading', { name: IAM_COPY.accessDenied.title })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: SHELL.admin.feedbackQueue })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: IAM_COPY.accessDenied.backToChat })).toBeInTheDocument()
  })

  it('retries the capability check without redirecting after identity changes', async () => {
    const probe = vi.fn()
      .mockResolvedValueOnce({ isAdmin: false, isAnonymousAdmin: false, tokenUsageEnabled: false })
      .mockResolvedValueOnce({ isAdmin: true, isAnonymousAdmin: false, tokenUsageEnabled: true })
    const invalidate = vi.fn()
    renderAt('/bedrock-chat/dashboard/feedback', { capabilityProbe: { probe, invalidate } })

    fireEvent.click(await screen.findByRole('button', { name: IAM_COPY.accessDenied.retry }))

    expect(await screen.findByRole('heading', { name: SHELL.admin.feedbackQueue })).toBeInTheDocument()
    expect(invalidate).toHaveBeenCalledOnce()
    expect(probe).toHaveBeenCalledTimes(2)
  })

  it('uses one HTTP probe across repeated admin navigations', async () => {
    const request = vi.fn().mockResolvedValue(ok({
      is_admin: true,
      anonymous: false,
      token_usage_enabled: true,
    }))
    const logger = fakeContainer().logger
    const capabilityProbe = new CapabilityHttpProbe('/admin', { request }, logger)
    const { router } = renderAt('/bedrock-chat/dashboard/feedback', { capabilityProbe })

    await screen.findByRole('heading', { name: SHELL.admin.feedbackQueue })
    await router.navigate({ to: '/dashboard/kb-browser' })
    await screen.findByRole('heading', { name: SHELL.admin.knowledge })

    expect(request).toHaveBeenCalledOnce()
  })

  it('hides and rejects Usage when token tracking is unavailable', async () => {
    renderAt('/bedrock-chat/dashboard/token-usages', {
      capabilityProbe: {
        probe: async () => ({ isAdmin: true, isAnonymousAdmin: false, tokenUsageEnabled: false }),
        invalidate: () => {},
      },
    })

    expect(await screen.findByText(IAM_COPY.usageUnavailable.title)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: SHELL.admin.usage })).not.toBeInTheDocument()
  })
})

describe('the shell around the routes', () => {
  // FR-SHELL-018 / FR-DS-008: one *primary* scroll container. FR-CONV-001 keeps the roster out of
  // the shell entirely until a principal is authenticated, which the fixture bootstrap is not.
  it('wraps chat routes in the chat layout with a single scroll container', async () => {
    const { dom } = renderAt('/bedrock-chat/ui')

    await findComposer()

    const main = dom.querySelector('main')
    expect(main?.classList.contains('overflow-y-auto')).toBe(true)
    expect(main?.querySelectorAll('.overflow-y-auto')).toHaveLength(0)
    expect(
      screen.queryByRole('navigation', { name: CONVERSATION_COPY.sidebar.label }),
    ).not.toBeInTheDocument()
  })

  it('shows an admin dashboard button in the chat header for an admin', async () => {
    renderAt('/bedrock-chat/ui')

    expect(await screen.findByRole('link', { name: SHELL.admin.openDashboard })).toHaveAttribute(
      'href',
      '/bedrock-chat/dashboard',
    )
  })

  it('hides the admin dashboard button in the chat header for a non-admin', async () => {
    renderAt('/bedrock-chat/ui', {
      capabilityProbe: {
        probe: async () => ({ isAdmin: false, isAnonymousAdmin: false, tokenUsageEnabled: false }),
        invalidate: () => {},
      },
    })

    await findComposer()

    expect(screen.queryByRole('link', { name: SHELL.admin.openDashboard })).not.toBeInTheDocument()
  })

  it('wraps admin routes in the admin layout with its section navigation', async () => {
    renderAt('/bedrock-chat/dashboard/token-usages')

    await screen.findByRole('heading', { name: SHELL.admin.usage })

    expect(within(screen.getByRole('banner')).getByText('Workload Analyzer')).toBeInTheDocument()
    const nav = screen.getByRole('navigation', { name: SHELL.admin.navigation })
    expect(within(nav).getAllByRole('link')).toHaveLength(5)
    expect(screen.getByRole('link', { name: SHELL.admin.backToChat })).toBeInTheDocument()
  })

  it('titles the chat route with the app title from the bootstrap payload', async () => {
    renderAt('/bedrock-chat/ui')

    await findComposer()

    expect(document.title).toBe('Workload Analyzer')
  })

  it('titles an admin route with its view name', async () => {
    renderAt('/bedrock-chat/dashboard/kb-browser')

    await screen.findByRole('heading', { name: SHELL.admin.knowledge })

    expect(document.title).toBe(`${SHELL.admin.knowledge} — Workload Analyzer`)
  })

  it('shows a dismissible dev-mode warning across admin navigation', async () => {
    const { router } = renderAt('/bedrock-chat/dashboard/feedback', {
      capabilityProbe: {
        probe: async () => ({ isAdmin: true, isAnonymousAdmin: true, tokenUsageEnabled: true }),
        invalidate: () => {},
      },
    })

    fireEvent.click(await screen.findByRole('button', { name: IAM_COPY.devMode.dismiss }))
    await router.navigate({ to: '/dashboard/kb-browser' })
    await screen.findByRole('heading', { name: SHELL.admin.knowledge })

    expect(screen.queryByText(IAM_COPY.devMode.title)).not.toBeInTheDocument()
  })
})
