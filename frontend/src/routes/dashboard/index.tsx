import { createFileRoute, redirect } from '@tanstack/react-router';

// The dashboard index lands on the feedback queue.
export const Route = createFileRoute('/dashboard/')({
  beforeLoad: () => {
    throw redirect({ to: '/dashboard/feedback', search: {} });
  },
});
