import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';

import { offsetParam, textParam } from '@/app/search-params';
import { SHELL } from '@/shared/copy/shell';
import { ReviewListPage, type ReviewListPatch } from '@/domains/review/presentation/ReviewListPage';

// FR-REV-002 / FR-REV-007: filters, offset and the open entry are the whole view state, so they
// live in the URL. The schema is colocated with the route that owns them (FR-SHELL-014); it moves
// to domains/review/application/ when that context lands.
export const reviewQueueSearchSchema = z.object({
  rating: z.enum(['all', 'positive', 'negative']).catch('all').default('all'),
  tags: textParam,
  from: textParam,
  to: textParam,
  offset: offsetParam,
  entry: textParam,
});

export type ReviewQueueSearch = z.output<typeof reviewQueueSearchSchema>;

function FeedbackQueueRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();

  return (
    <ReviewListPage
      title={SHELL.admin.feedbackQueue}
      mode="queue"
      search={search}
      onSearchChange={(patch: ReviewListPatch) => {
        void navigate({ search: (current) => ({ ...current, ...patch }) });
      }}
    />
  );
}

export const Route = createFileRoute('/dashboard/feedback/')({
  validateSearch: reviewQueueSearchSchema,
  staticData: { title: SHELL.admin.feedbackQueue },
  component: FeedbackQueueRoute,
});
