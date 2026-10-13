export type NotificationKind = 'success' | 'error' | 'info' | 'warning'

export type NotifyOptions = {
  readonly description?: string
  // Overrides the 4 000 ms default (FR-SHELL-007); 0 keeps the toast until dismissed.
  readonly durationMs?: number
}

// SPEC-021 §5. Replaces the legacy `showToast` and every `window.alert`. A port, so a test can
// record what the app said without a DOM (STD-002 §4).
export interface NotificationPort {
  success(message: string, options?: NotifyOptions): void
  error(message: string, options?: NotifyOptions): void
  info(message: string, options?: NotifyOptions): void
  warning(message: string, options?: NotifyOptions): void
}
