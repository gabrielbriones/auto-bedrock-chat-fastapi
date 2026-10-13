// FR-PROMPT-003: SCREAMING_SNAKE_CASE → Title Case, e.g. `JOB_ID` → "Job Id". Parity with the
// legacy client's `_prettifyVarName`, used whenever the backend doesn't supply an explicit `label`.
export const deriveLabel = (name: string): string =>
  name
    .split('_')
    .filter((word) => word.length > 0)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ')
