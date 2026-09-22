export const CREDENTIAL_KINDS = [
  'bearer_token',
  'basic_auth',
  'api_key',
  'oauth2_client_credentials',
  'custom',
  'sso',
] as const

export type CredentialKind = (typeof CREDENTIAL_KINDS)[number]

// The backend advertises and accepts `oauth2` as an alias of the canonical AuthType value
// (`DEFAULT_SUPPORTED_AUTH_TYPES` vs `AuthType.OAUTH2_CLIENT_CREDENTIALS`).
export const toCredentialKind = (authType: string): CredentialKind | null => {
  const canonical = authType === 'oauth2' ? 'oauth2_client_credentials' : authType
  return (CREDENTIAL_KINDS as readonly string[]).includes(canonical)
    ? (canonical as CredentialKind)
    : null
}

export const DEFAULT_API_KEY_HEADER = 'X-API-Key'

export type BearerTokenCredential = {
  readonly kind: 'bearer_token'
  readonly token: string
}

export type BasicAuthCredential = {
  readonly kind: 'basic_auth'
  readonly username: string
  readonly password: string
}

export type ApiKeyCredential = {
  readonly kind: 'api_key'
  readonly apiKey: string
  readonly header: string
}

export type OAuth2ClientCredentials = {
  readonly kind: 'oauth2_client_credentials'
  readonly clientId: string
  readonly clientSecret: string
  readonly tokenUrl: string
  readonly scope?: string
}

export type CustomCredential = {
  readonly kind: 'custom'
  readonly headers: Readonly<Record<string, string>>
}

export type SsoCredential = {
  readonly kind: 'sso'
}

export type Credential =
  | BearerTokenCredential
  | BasicAuthCredential
  | ApiKeyCredential
  | OAuth2ClientCredentials
  | CustomCredential
  | SsoCredential

export type CredentialDraft = Readonly<Record<string, unknown>>

export type CredentialField =
  | 'token'
  | 'username'
  | 'password'
  | 'apiKey'
  | 'header'
  | 'clientId'
  | 'clientSecret'
  | 'tokenUrl'
  | 'scope'
  | 'headers'

export type FieldErrors = Readonly<Partial<Record<CredentialField, string>>>