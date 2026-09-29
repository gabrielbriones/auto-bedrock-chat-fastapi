import { createFileRoute } from '@tanstack/react-router';

import { EmptyState } from '@/components/ui/composed/empty-state'
import { IAM_COPY } from '@/shared/copy/iam'
import { SHELL } from '@/shared/copy/shell';
import { KbSourcesPage } from '@/domains/knowledge/presentation/KbSourcesPage'

class KbSourcesUnavailableError extends Error {}

// The KB source-ingestion view has no URL state of its own: the single global run is server
// state, polled, and the forms are drafts. Gated on `kb_source_ingestion_enabled`, the same
// condition under which the backend registers `/admin/kb/sources/*` at all.
export const Route = createFileRoute('/dashboard/kb-sources')({
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
  return <KbSourcesPage title={SHELL.admin.kbSources} description={SHELL.admin.descriptions.kbSources} />
}
