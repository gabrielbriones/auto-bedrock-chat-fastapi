import { createFileRoute, redirect } from '@tanstack/react-router';

// SPEC-021 §2: /chat/ui/admin itself has no view — it lands on the review queue.
export const Route = createFileRoute('/dashboard/')({
  beforeLoad: () => {
    throw redirect({ to: '/admin/feedback', search: {} });
  },
});
