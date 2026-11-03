// Small, per-browser user preferences. Implementations swallow storage failures (private modes,
// quota): a preference is never worth breaking the app over.
export interface PreferenceStoragePort {
  read(key: string): string | null
  write(key: string, value: string): void
}
