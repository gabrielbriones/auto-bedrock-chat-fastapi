// Frontend-facing shape of BC-001's `GET {chat}/config` response (CONTRACT-001 §6). Splitting
// this into `IamPolicy` / `PromptCatalog` / `ConfigurationProfile` / `FeatureFlags` (DESIGN-001 §8)
// happens once those bounded contexts land; today's consumers (container, provider) need one type.
export type PresetPromptConfig = {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly template: string;
  readonly group?: string | undefined;
};

export type AvailableModel = {
  readonly id: string;
  readonly name: string;
  readonly provider: string;
  readonly supportsTemperature: boolean;
  readonly maxOutputTokens: number;
};

export type AvailableModelGroup = {
  readonly provider: string;
  readonly models: readonly AvailableModel[];
};

export type ChatBootstrap = {
  readonly websocketUrl: string;
  readonly authEnabled: boolean;
  readonly requireAuth: boolean;
  readonly supportedAuthTypes: readonly string[];
  readonly defaultAuthType: string;
  readonly modelId: string;
  readonly presetPrompts: readonly PresetPromptConfig[];
  readonly variables: readonly Readonly<Record<string, unknown>>[];
  readonly ssoEnabled: boolean;
  readonly ssoLoginUrl: string;
  readonly ssoLogoutUrl: string;
  readonly ssoAuthenticated: boolean;
  readonly ssoUserDisplay: string | null;
  readonly feedbackEnabled: boolean;
  readonly lockInputWhileResponding: boolean;
  readonly adminEnabled: boolean;
  readonly adminPrefix: string;
  readonly dashboardUrl: string;
  readonly conversationPersistenceEnabled: boolean;
  readonly enableConfigSidebar: boolean;
  readonly allowedDynamicOverrides: readonly string[] | null;
  readonly availableModels: readonly AvailableModel[];
  readonly availableModelGroups: readonly AvailableModelGroup[];
  readonly overrideDefaults: Readonly<Record<string, unknown>>;
  readonly uiTitle: string;
  readonly appTitle: string;
  readonly modelDisplayName: string;
  readonly uiWelcomeMessage: string;
};
