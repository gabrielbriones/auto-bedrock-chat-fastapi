export type Capabilities = {
  readonly isAdmin: boolean
  readonly isAnonymousAdmin: boolean
  readonly tokenUsageEnabled: boolean
}

export const NO_CAPABILITIES: Capabilities = Object.freeze({
  isAdmin: false,
  isAnonymousAdmin: false,
  tokenUsageEnabled: false,
})