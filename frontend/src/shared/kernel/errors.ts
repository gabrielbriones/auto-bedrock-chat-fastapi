// CatchBoundary and other render-error paths type the caught value as `unknown`; callers
// throughout the app assume a narrowed `Error`.
export const toError = (error: unknown): Error =>
  error instanceof Error ? error : new Error(String(error))
