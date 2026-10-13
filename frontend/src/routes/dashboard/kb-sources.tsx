import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';

import { offsetParam } from '@/app/search-params';
import { EmptyState } from '@/components/ui/composed/empty-state'
import { IAM_COPY } from '@/shared/copy/iam'
import { SHELL } from '@/shared/copy/shell';
import { KbSourcesPage, type KbSourcesPatch } from '@/domains/knowledge/presentation/KbSourcesPage'

class KbSourcesUnavailableError extends Error {}

// The sources-list filter and offset are URL state; the single global run is server state,
// polled, and the forms are drafts. Gated on `kb_source_ingestion_enabled`, the same
// condition under which the backend registers `/admin/kb/sources/*` at all.
export const kbSourcesSearchSchema = z.object({
  type: z.enum(['web', 'file', 'feedback']).optional().catch(undefined),
  offset: offsetParam,
});

export type KbSourcesSearch = z.output<typeof kbSourcesSearchSchema>;

export const Route = createFileRoute('/dashboard/kb-sources')({
  validateSearch: kbSourcesSearchSchema,
  staticData: { title: SHELL.admin.kbSources },
  beforeLoad: ({ context }) => {
    if (!context.capabilities.kbSourceIngestionEnabled) {
      throw new KbSourcesUnavailableError()
    }
  },
  errorComponent: ({ error }) => {
    if (!(error instanceof KbSourcesUnavailableError)) {
      throw error
    }

    return (
      <EmptyState
        title={IAM_COPY.kbSourcesUnavailable.title}
        description={IAM_COPY.kbSourcesUnavailable.description}
      />
    )
  },
  component: KbSourcesRoute,
});

function KbSourcesRoute() {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()

  return (
    <KbSourcesPage
      title={SHELL.admin.kbSources}
      description={SHELL.admin.descriptions.kbSources}
      search={search}
      onSearchChange={(patch: KbSourcesPatch) => {
        void navigate({ search: (current) => ({ ...current, ...patch }) })
      }}
    />
  )
}
