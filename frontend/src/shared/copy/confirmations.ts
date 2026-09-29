// Provisional home for confirmation copy until each owning context's presentation layer lands
// (conversation delete: SPEC-011 FR-CONV-005; feedback bulk delete: SPEC-016 FR-REV-011a).
// FR-DS-011: every user-facing string lives here as a typed constant, never inline in JSX.
export const CONFIRMATIONS = {
  deleteConversation: (title: string) => `Delete "${title}"? This cannot be undone.`,
  deleteRejectedFeedbackEntry: 'Delete this rejected entry? This cannot be undone.',
} as const
