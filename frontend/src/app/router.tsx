import { createRouter } from '@tanstack/react-router'
import type { RouterHistory } from '@tanstack/react-router'

import type { Container } from '@/app/bootstrap/container'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { routeTree } from '@/routeTree.gen'

export type CreateAppRouterOptions = {
  // Tests and the future SSR-free preview harness supply a memory history so the router is
  // constructible without a DOM (STD-002 §4).
  readonly history?: RouterHistory
}

// Both the chat and dashboard routes live under the same deployed prefix.
export const createAppRouter = (container: Container, options: CreateAppRouterOptions = {}) =>
  createRouter({
    routeTree,
    basepath: '/bedrock-chat',
    context: { container },
    defaultPreload: 'intent',
    // FR-SHELL-015: a first visit to a split route (chunk fetch + beforeLoad) must show progress
    // almost immediately instead of leaving the previous page frozen under the click.
    defaultPendingComponent: () => <LoadingState rows={4} />,
    defaultPendingMs: 150,
    defaultPendingMinMs: 300,
    // The chat routes remount the shell's <main>, and the router's restoration for it (a stale
    // position from a different conversation, or a reset to 0) lands after the transcript has
    // already pinned itself to the latest message. Those routes own that scroller themselves.
    scrollRestoration: ({ location }) => !/\/ui(\/|$)/.test(location.pathname),
    // FR-SHELL-023: every caught render error is reported once, with the route and nothing the
    // user typed. The boundaries themselves only decide what to draw.
    defaultOnCatch: (error) => {
      container.logger.error('render_error', { name: error.name, message: error.message })
    },
    ...(options.history !== undefined ? { history: options.history } : {}),
  })

export type AppRouter = ReturnType<typeof createAppRouter>

declare module '@tanstack/react-router' {
  interface Register {
    router: AppRouter
  }

  // FR-SHELL-024: the view name a route contributes to the document title.
  interface StaticDataRouteOption {
    title?: string
  }
}
