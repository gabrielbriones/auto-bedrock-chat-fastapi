import { linkOptions } from '@tanstack/react-router'

import type { ReviewQueueSearch } from '@/routes/admin/feedback.index'
import type { ReviewStatsSearch } from '@/routes/admin/feedback.stats'
import type { ReviewedSearch } from '@/routes/admin/feedback.reviewed'
import type { KnowledgeSearch } from '@/routes/admin/knowledge'
import type { UsageSearch } from '@/routes/admin/usage'

// FR-SHELL-014a: navigation targets are built here, type-checked against the route tree and the
// owning route's search schema, so a mistyped param name is a compile error rather than a
// silently ignored query string.
export const chatLink = () => linkOptions({ to: '/' })

export const conversationLink = (conversationId: string) =>
  linkOptions({ to: '/c/$conversationId', params: { conversationId } })

export const reviewQueueLink = (search: ReviewQueueSearch) =>
  linkOptions({ to: '/admin/feedback', search })

export const reviewedLink = (search: ReviewedSearch) =>
  linkOptions({ to: '/admin/feedback/reviewed', search })

export const reviewStatsLink = (search: ReviewStatsSearch) =>
  linkOptions({ to: '/admin/feedback/stats', search })

export const knowledgeLink = (search: KnowledgeSearch) =>
  linkOptions({ to: '/admin/knowledge', search })

export const usageLink = (search: UsageSearch) => linkOptions({ to: '/admin/usage', search })
