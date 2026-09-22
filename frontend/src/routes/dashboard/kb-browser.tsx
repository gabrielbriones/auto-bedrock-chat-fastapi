import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';

import { booleanParam, offsetParam, textParam } from '@/app/search-params';
import { KnowledgeBrowserPage, type KnowledgeBrowserPatch } from '@/domains/knowledge/presentation/KnowledgeBrowserPage'
import { SHELL } from '@/shared/copy/shell';

// FR-KB-001 / FR-KB-007: every filter, the offset and the open document are URL state. `doc` may
// contain slashes (FR-KB-009) — the router encodes it as a search value, never a path segment.
export const knowledgeSearchSchema = z.object({
  source: textParam,
  topic: textParam,
  tags: textParam,
  from: textParam,
  to: textParam,
  flagged: booleanParam(false),
  offset: offsetParam,
  doc: textParam,
});

export type KnowledgeSearch = z.output<typeof knowledgeSearchSchema>;

export const Route = createFileRoute('/dashboard/kb-browser')({
  validateSearch: knowledgeSearchSchema,
  staticData: { title: SHELL.admin.knowledge },
  component: KnowledgeRoute,
});

function KnowledgeRoute() {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()

  return (
    <KnowledgeBrowserPage
      title={SHELL.admin.knowledge}
      search={search}
      onSearchChange={(patch: KnowledgeBrowserPatch) => {
        void navigate({ search: (current) => ({ ...current, ...patch }) })
      }}
    />
  )
}
