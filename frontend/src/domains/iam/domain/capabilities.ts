export type Capabilities = {
  readonly isAdmin: boolean
  readonly isAnonymousAdmin: boolean
  readonly tokenUsageEnabled: boolean
  // `true` when a KB store is configured, which is also the condition under which the
  // `/admin/kb/sources/*` routes are registered at all.
  readonly kbSourceIngestionEnabled: boolean
}

export const NO_CAPABILITIES: Capabilities = Object.freeze({
  isAdmin: false,
  isAnonymousAdmin: false,
  tokenUsageEnabled: false,
  kbSourceIngestionEnabled: false,
})