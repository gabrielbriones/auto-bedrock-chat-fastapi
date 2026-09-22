import { createFileRoute } from '@tanstack/react-router';
import { z } from 'zod';

import { textParam } from '@/app/search-params';
import { SHELL } from '@/shared/copy/shell';
import { ReviewStatsPage } from '@/domains/review/presentation/ReviewStatsPage';

// Stats are a date window only — no list, so no offset and no open entry.
export const reviewStatsSearchSchema = z.object({
  from: textParam,
  to: textParam,
});

export type ReviewStatsSearch = z.output<typeof reviewStatsSearchSchema>;

export const Route = createFileRoute('/dashboard/feedback/stats')({
  validateSearch: reviewStatsSearchSchema,
  staticData: { title: SHELL.admin.stats },
  component: () => <ReviewStatsPage />,
});
