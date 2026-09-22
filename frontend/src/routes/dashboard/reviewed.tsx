import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';

import { offsetParam, textParam } from '@/app/search-params';
import { SHELL } from '@/shared/copy/shell';
import { ReviewListPage, type ReviewListPatch } from '@/domains/review/presentation/ReviewListPage';

// FR-REV-007: the reviewed list carries the same shareable state as the queue, minus the rating
// filter it does not offer.
export const reviewedSearchSchema = z.object({
  decision: z.enum(['approved', 'rejected', 'all']).catch('approved').default('approved'),
  tags: textParam,
  from: textParam,
  to: textParam,
  offset: offsetParam,
  entry: textParam,
});

export type ReviewedSearch = z.output<typeof reviewedSearchSchema>;

function ReviewedRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();

  return (
    <ReviewListPage
      title={SHELL.admin.reviewed}
      mode="reviewed"
      search={search}
      onSearchChange={(patch: ReviewListPatch) => {
        void navigate({ search: (current) => ({ ...current, ...patch }) });
      }}
    />
  );
}

export const Route = createFileRoute('/dashboard/reviewed')({
  validateSearch: reviewedSearchSchema,
  staticData: { title: SHELL.admin.reviewed },
  component: ReviewedRoute,
});
