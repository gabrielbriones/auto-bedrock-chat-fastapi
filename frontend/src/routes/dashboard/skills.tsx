import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';

import { textParam } from '@/app/search-params';
import { EmptyState } from '@/components/ui/composed/empty-state'
import { IAM_COPY } from '@/shared/copy/iam'
import { SHELL } from '@/shared/copy/shell';
import { SkillsPage, type SkillsSearch } from '@/domains/skills/presentation/SkillsPage'

class SkillsUnavailableError extends Error {}

// Gated on `skills_enabled`, the same condition under which the backend registers `/admin/skills*`.
export const skillsSearchSchema = z.object({
  q: textParam,
});

export const Route = createFileRoute('/dashboard/skills')({
  validateSearch: skillsSearchSchema,
  staticData: { title: SHELL.admin.skills },
  beforeLoad: ({ context }) => {
    if (!context.capabilities.skillsEnabled) {
      throw new SkillsUnavailableError()
    }
  },
  errorComponent: ({ error }) => {
    if (!(error instanceof SkillsUnavailableError)) {
      throw error
    }

    return (
      <EmptyState
        title={IAM_COPY.skillsUnavailable.title}
        description={IAM_COPY.skillsUnavailable.description}
      />
    )
  },
  component: SkillsRoute,
});

function SkillsRoute() {
  const search = Route.useSearch()
  const navigate = Route.useNavigate()

  return (
    <SkillsPage
      title={SHELL.admin.skills}
      description={SHELL.admin.descriptions.skills}
      search={search}
      onSearchChange={(patch: SkillsSearch) => {
        void navigate({ search: (current) => ({ ...current, ...patch }) })
      }}
    />
  )
}
