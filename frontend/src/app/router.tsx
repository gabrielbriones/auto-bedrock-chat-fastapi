import { createRouter } from '@tanstack/react-router'
import type { RouterHistory } from '@tanstack/react-router'

import type { Container } from '@/app/bootstrap/container'
import { routeTree } from '@/routeTree.gen'

export type CreateAppRouterOptions = {
  // Tests and the future SSR-free preview harness supply a memory history so the router is
  // constructible without a DOM (STD-002 §4).
  readonly history?: RouterHistory
}

// ADR-014: a client-only router over a static SPA build. `basepath` must stay in step with
// `base: '/chat/ui/'` in vite.config.ts — the deployed sub-path from BC-002.
export const createAppRouter = (container: Container, options: CreateAppRouterOptions = {}) =>
  createRouter({
    routeTree,
    basepath: '/chat/ui',
    context: { container },
    defaultPreload: 'intent',
    scrollRestoration: true,
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
