// Wire-shaped sample matching tests/msw/fixtures/bootstrap-config.json (CONTRACT-001 §6).
// Duplicated here (rather than imported) because tsconfig.app.json's project boundary
// deliberately excludes tests/** (see tests/tsconfig.json) — keep the two in sync by hand.
// Falls back to the .env.example value so the suite runs without a local .env.
export const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export const bootstrapConfigFixture: Record<string, unknown> = {
  websocketUrl: `${apiUrl.replace(/^http/, 'ws')}/bedrock-chat/ws`,
  authEnabled: true,
  requireAuth: false,
  supportedAuthTypes: ['bearer', 'basic', 'api_key', 'oauth2', 'custom', 'sso'],
  defaultAuthType: 'bearer',
  modelId: 'claude-3-5-sonnet',
  presetPrompts: [],
  variables: [],
  ssoEnabled: false,
  ssoLoginUrl: '/bedrock-chat/auth/sso/login',
  ssoLogoutUrl: '/bedrock-chat/auth/sso/logout',
  ssoAuthenticated: false,
  ssoUserDisplay: null,
  feedbackEnabled: true,
  lockInputWhileResponding: true,
  adminEnabled: false,
  adminPrefix: '/bedrock-chat/admin',
  dashboardUrl: '/bedrock-chat/dashboard',
  conversationPersistenceEnabled: true,
  enableConfigSidebar: true,
  allowedDynamicOverrides: null,
  availableModels: [
    {
      id: 'us.anthropic.claude-sonnet-5',
      name: 'Claude Sonnet 5 (US)',
      provider: 'Anthropic',
      supports_temperature: false,
      max_output_tokens: 128000,
    },
  ],
  availableModelGroups: [
    {
      provider: 'Anthropic',
      models: [
        {
          id: 'us.anthropic.claude-sonnet-5',
          name: 'Claude Sonnet 5 (US)',
          provider: 'Anthropic',
          supports_temperature: false,
          max_output_tokens: 128000,
        },
      ],
    },
  ],
  overrideDefaults: {},
  uiTitle: 'Workload Analyzer',
  appTitle: 'Workload Analyzer',
  modelDisplayName: 'Claude 3.5 Sonnet',
  uiWelcomeMessage: 'How can I help you analyze your workload today?',
};
