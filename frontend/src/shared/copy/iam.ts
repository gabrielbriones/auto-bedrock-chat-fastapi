// FR-DS-011 / NFR-I18N-001 — every user-facing `iam` string is a typed constant here, never
// inline in JSX. Strings SPEC-010 quotes verbatim are additionally listed in `legacy-strings.json`
// so `npm run check:copy` guards them against drift.
export const IAM_COPY = {
  dialog: {
    title: 'Authenticate',
    description: 'Choose how you want to prove your identity for this session.',
    kindLabel: 'Authentication Type',
    kindPlaceholder: '-- Select Auth Type --',
    info: 'Authentication credentials will be stored securely in your session and applied to all API calls.',
    submit: 'Authenticate',
    submitting: 'Authenticating…',
    skip: 'Skip',
  },

  // FR-IAM-001 display text: underscores → spaces, capitalised; `sso` is the one special case.
  // Spelled out rather than derived so the strings are translatable.
  kinds: {
    bearer_token: 'Bearer token',
    basic_auth: 'Basic auth',
    api_key: 'API key',
    oauth2_client_credentials: 'OAuth2 client credentials',
    custom: 'Custom',
    sso: 'Login with SSO',
  },

  fields: {
    token: { label: 'Bearer Token', placeholder: 'Enter your JWT or OAuth token' },
    username: { label: 'Username', placeholder: 'Enter username' },
    password: { label: 'Password', placeholder: 'Enter password' },
    apiKey: { label: 'API Key', placeholder: 'Enter your API key' },
    header: { label: 'API Key Header', placeholder: 'e.g., X-API-Key' },
    clientId: { label: 'Client ID', placeholder: 'Enter client ID' },
    clientSecret: { label: 'Client Secret', placeholder: 'Enter client secret' },
    tokenUrl: { label: 'Token URL', placeholder: 'https://auth.example.com/token' },
    scope: { label: 'Scope (optional)', placeholder: 'e.g., api:read api:write' },
    headers: {
      label: 'Custom Headers (JSON)',
      placeholder: '{"X-Custom-Header": "value", "X-API-Version": "v2"}',
    },
  },

  sso: {
    description: "Click below to log in with your organization's identity provider.",
    login: 'Login with SSO',
    redirecting: 'Redirecting…',
  },

  // FR-IAM-016
  status: {
    logIn: 'Log in',
    logOut: 'Log out',
    signedInAs: (displayName: string) => `Signed in as ${displayName}`,
    account: 'Account',
  },

  errors: {
    notConnected: 'Not connected. Your credentials will be sent as soon as the connection returns.',
  },

  validation: {
    required: 'This field is required.',
    invalidJson: 'Invalid JSON syntax.',
    invalidHeaderMap: 'Must be a JSON object mapping header names to string values.',
  },

  accessDenied: {
    title: 'Access denied',
    description: 'You do not have admin access to this dashboard.',
    retry: 'Retry access check',
    backToChat: 'Back to chat',
  },

  devMode: {
    title: 'Dev mode — anonymous admin',
    description: 'require_tool_auth=false is active; every request is treated as admin. Do not use this configuration in production.',
    dismiss: 'Dismiss dev mode warning',
  },

  usageUnavailable: {
    title: 'Usage unavailable',
    description: 'Token usage tracking is not enabled for this deployment.',
  },
} as const