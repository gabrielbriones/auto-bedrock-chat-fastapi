// Copy shared by every admin surface built on the SPEC-020 §3.2 primitives — the review queue,
// the knowledge browser and usage analytics all paginate and filter the same way, so these strings
// live here rather than in any one context's module (FR-DS-011, NFR-I18N-001).
export const ADMIN_COPY = {
  pagination: {
    label: 'Pagination',
    // FR-REV-009, verbatim from the legacy dashboard.
    range: (from: number, to: number, total: number) => `Showing ${from}–${to} of ${total}`,
    // FIX-06: a page that has been emptied still says where you are and still offers a way back.
    emptyPage: 'No results on this page',
    previous: 'Previous',
    next: 'Next',
  },

  table: {
    loading: 'Loading results',
    open: (label: string) => `Open ${label}`,
  },

  filters: {
    label: 'Filters',
    reset: 'Reset',
  },
} as const
