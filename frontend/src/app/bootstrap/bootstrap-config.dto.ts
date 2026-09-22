import { z } from 'zod';

import { err, ok, type Result } from '@/shared/kernel/result';

import type { ChatBootstrap } from '@/app/bootstrap/chat-bootstrap';

// Wire shape for BC-001's `GET {chat}/config` (CONTRACT-001 §6), already camelCased by the
// backend mapper. Unknown keys are ignored by default `z.object` (not `.strict()`/`.passthrough()`).
const presetPromptSchema = z.object({
  id: z.string(),
  label: z.string(),
  description: z.string(),
  template: z.string(),
  group: z.string().optional(),
});

// The backend only camelCases top-level `ChatConfig` keys; nested model objects come through
// as the raw provider-catalog shape (snake_case), so this one is translated explicitly.
const availableModelSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    provider: z.string(),
    supports_temperature: z.boolean(),
    max_output_tokens: z.number(),
  })
  .transform(({ supports_temperature, max_output_tokens, ...rest }) => ({
    ...rest,
    supportsTemperature: supports_temperature,
    maxOutputTokens: max_output_tokens,
  }));

const availableModelGroupSchema = z.object({
  provider: z.string(),
  models: z.array(availableModelSchema),
});

const bootstrapConfigSchema = z.object({
  websocketUrl: z.string(),
  authEnabled: z.boolean(),
  requireAuth: z.boolean(),
  supportedAuthTypes: z.array(z.string()),
  defaultAuthType: z.string(),
  modelId: z.string(),
  presetPrompts: z.array(presetPromptSchema),
  variables: z.array(z.record(z.string(), z.unknown())),
  ssoEnabled: z.boolean(),
  ssoLoginUrl: z.string(),
  ssoLogoutUrl: z.string(),
  ssoAuthenticated: z.boolean(),
  ssoUserDisplay: z.string().nullable(),
  feedbackEnabled: z.boolean(),
  lockInputWhileResponding: z.boolean(),
  adminEnabled: z.boolean(),
  adminPrefix: z.string(),
  dashboardUrl: z.string(),
  conversationPersistenceEnabled: z.boolean(),
  enableConfigSidebar: z.boolean(),
  allowedDynamicOverrides: z.array(z.string()).nullable(),
  availableModels: z.array(availableModelSchema),
  availableModelGroups: z.array(availableModelGroupSchema),
  overrideDefaults: z.record(z.string(), z.unknown()),
  uiTitle: z.string(),
  appTitle: z.string(),
  modelDisplayName: z.string(),
  uiWelcomeMessage: z.string(),
});

export type BootstrapParseError = {
  readonly code: 'invalid-response';
  readonly title: string;
  readonly issues: readonly string[];
};

// Anti-corruption layer entry point (DESIGN-001 §8): the only place allowed to know the
// bootstrap wire shape. Never throws — malformed/incomplete input returns `err`.
export const toChatBootstrap = (input: unknown): Result<ChatBootstrap, BootstrapParseError> => {
  const parsed = bootstrapConfigSchema.safeParse(input);

  if (!parsed.success) {
    return err({
      code: 'invalid-response',
      title: 'Invalid bootstrap configuration',
      issues: parsed.error.issues.map((issue) => `${issue.path.join('.') || '(root)'}: ${issue.message}`),
    });
  }

  return ok(parsed.data);
};
