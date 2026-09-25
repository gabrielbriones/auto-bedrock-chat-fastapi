import { linkOptions } from '@tanstack/react-router'

import type { ReviewQueueSearch } from '@/routes/dashboard/feedback.index'
import type { ReviewStatsSearch } from '@/routes/dashboard/feedback.stats'
import type { ReviewedSearch } from '@/routes/dashboard/reviewed'
import type { KnowledgeSearch } from '@/routes/dashboard/kb-browser'
import type { UsageSearch } from '@/routes/dashboard/token-usages'

// FR-SHELL-014a: navigation targets are built here, type-checked against the route tree and the
// owning route's search schema, so a mistyped param name is a compile error rather than a
// silently ignored query string.
export const chatLink = () => linkOptions({ to: '/ui' })

export const conversationLink = (conversationId: string) =>
  linkOptions({ to: '/ui/c/$conversationId', params: { conversationId } })

export const reviewQueueLink = (search: ReviewQueueSearch) =>
  linkOptions({ to: '/dashboard/feedback', search })

export const reviewedLink = (search: ReviewedSearch) =>
  linkOptions({ to: '/dashboard/reviewed', search })

export const reviewStatsLink = (search: ReviewStatsSearch) =>
  linkOptions({ to: '/dashboard/feedback/stats', search })

export const knowledgeLink = (search: KnowledgeSearch) =>
  linkOptions({ to: '/dashboard/kb-browser', search })

export const usageLink = (search: UsageSearch) => linkOptions({ to: '/dashboard/token-usages', search })
