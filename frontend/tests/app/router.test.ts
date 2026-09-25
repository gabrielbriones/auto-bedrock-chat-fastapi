import { describe, expect, it } from '@jest/globals'
import { createMemoryHistory } from '@tanstack/react-router'
import type { z } from 'zod'

import { fakeContainer } from './bootstrap/container.fixture'
import {
  chatLink,
  conversationLink,
  knowledgeLink,
  reviewQueueLink,
  reviewStatsLink,
  reviewedLink,
  usageLink,
} from '@/app/route-links'
import { createAppRouter } from '@/app/router'
import { reviewQueueSearchSchema } from '@/routes/dashboard/feedback.index'
import { reviewedSearchSchema } from '@/routes/dashboard/reviewed'
import { reviewStatsSearchSchema } from '@/routes/dashboard/feedback.stats'
import { knowledgeSearchSchema } from '@/routes/dashboard/kb-browser'
import { usageSearchSchema } from '@/routes/dashboard/token-usages'

const router = () =>
  createAppRouter(fakeContainer(), { history: createMemoryHistory({ initialEntries: ['/bedrock-chat/ui'] }) })

describe('the application router', () => {
  it('resolves every route in the SPEC-021 §2 table', () => {
    expect(Object.keys(router().routesById).sort()).toEqual([
      '/',
      '/dashboard',
      '/dashboard/',
      '/dashboard/feedback/',
      '/dashboard/feedback/stats',
      '/dashboard/kb-browser',
      '/dashboard/reviewed',
      '/dashboard/token-usages',
      '/ui/',
      '/ui/c/$conversationId',
      '__root__',
    ])
  })

  it('serves the whole tree under the deployed /ui base path', () => {
    expect(router().buildLocation(chatLink()).href).toBe('/bedrock-chat/ui')
    expect(router().buildLocation(conversationLink('abc-123')).href).toBe('/bedrock-chat/ui/c/abc-123')
  })

  it('gives the admin subtree a guard that resolves through the typed route context', () => {
    expect(router().routesById['/dashboard'].options.beforeLoad).toBeTypeOf('function')
  })
})

describe('typed navigation', () => {
  it('builds an admin URL from a validated search state', () => {
    const link = reviewQueueLink({ rating: 'negative', tags: 'latency', offset: 50 })

    expect(router().buildLocation(link).href).toBe(
      '/bedrock-chat/dashboard/feedback?rating=negative&tags=latency&offset=50',
    )
  })

  it('round-trips every route search state through the URL it produces', () => {
    const appRouter = router()

    const cases = [
      {
        schema: reviewQueueSearchSchema,
        link: reviewQueueLink({
          rating: 'positive',
          tags: 'tone',
          from: '2026-01-01',
          to: '2026-02-01',
          offset: 100,
          entry: 'entry-7',
        }),
      },
      {
        schema: reviewedSearchSchema,
        link: reviewedLink({ decision: 'approved', tags: 'tone', offset: 0 }),
      },
      { schema: reviewStatsSearchSchema, link: reviewStatsLink({ from: '2026-01-01' }) },
      {
        schema: knowledgeSearchSchema,
        link: knowledgeLink({
          source: 'feedback',
          flagged: true,
          offset: 50,
          doc: 'docs/guide/a.md',
        }),
      },
      { schema: usageSearchSchema, link: usageLink({ user: 'someone', offset: 0 }) },
    ] satisfies ReadonlyArray<{ schema: z.ZodType; link: { to: string } }>

    for (const { schema, link } of cases) {
      const location = appRouter.buildLocation(link)

      expect(schema.parse(appRouter.options.parseSearch(location.searchStr))).toEqual(
        location.search,
      )
    }
  })

  it('renders a hand-edited URL rather than throwing on it', () => {
    const search = knowledgeSearchSchema.parse(
      router().options.parseSearch('?flagged=maybe&offset=-4&rogue=1'),
    )

    expect(search).toEqual({ flagged: false, offset: 0 })
  })
})
