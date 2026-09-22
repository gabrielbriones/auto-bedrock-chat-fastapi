import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';

import { offsetParam, textParam } from '@/app/search-params';
import { EmptyState } from '@/components/ui/composed/empty-state'
import { IAM_COPY } from '@/shared/copy/iam'
import { SHELL } from '@/shared/copy/shell';
import { UsageAnalyticsPage } from '@/domains/telemetry/presentation/UsageAnalyticsPage'
import type { UsageSearchPatch } from '@/domains/telemetry/presentation/usage-search'

class UsageUnavailableError extends Error {}

// FR-TEL-014: the applied query is shareable and restorable. The `token_usage_enabled` gate on
// this route is Task 12's, alongside the admin capability guard.
export const usageSearchSchema = z.object({
  topLimit: z.coerce.number().int().refine((value) => [5, 10, 20, 50, 100].includes(value)).optional().catch(10),
  user: textParam,
  from: textParam,
  to: textParam,
  offset: offsetParam,
});

export type UsageSearch = z.output<typeof usageSearchSchema>;

export const Route = createFileRoute('/dashboard/token-usages')({
  validateSearch: usageSearchSchema,
  staticData: { title: SHELL.admin.usage },
  beforeLoad: ({ context }) => {
    if (!context.capabilities.tokenUsageEnabled) {
      throw new UsageUnavailableError()
    }
  },
  errorComponent: ({ error }) => {
    if (!(error instanceof UsageUnavailableError)) {
      throw error
    }

    return (
      <EmptyState
        title={IAM_COPY.usageUnavailable.title}
        description={IAM_COPY.usageUnavailable.description}
      />
    )
  },
  component: UsageRoute,
});

function UsageRoute() {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()

  return (
    <UsageAnalyticsPage
      title={SHELL.admin.usage}
      search={search}
      onSearchChange={(patch: UsageSearchPatch) => {
        void navigate({ search: (current) => ({ ...current, ...patch }) })
      }}
    />
  )
}
