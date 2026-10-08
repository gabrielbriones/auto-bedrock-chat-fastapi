import { describe, expect, it } from '@jest/globals';

import { isErr, isOk } from '@/shared/kernel/result';

import { apiUrl, bootstrapConfigFixture as bootstrapConfig } from './bootstrap-config.fixture';
import { toChatBootstrap } from '@/app/bootstrap/bootstrap-config.dto';

describe('toChatBootstrap', () => {
  it('parses a valid CONTRACT-001 §6 payload into a ChatBootstrap', () => {
    const result = toChatBootstrap(bootstrapConfig);

    expect(isOk(result)).toBe(true);
    if (isOk(result)) {
      expect(result.value).toMatchObject({
        websocketUrl: `${apiUrl.replace(/^http/, 'ws')}/chat/ws`,
        uiEndpoint: '/chat/ui',
        authEnabled: true,
        authExpirationBehaviour: 'both',
        ssoSessionRenewalIntervalSeconds: 900,
        adminPrefix: '/chat/admin',
        uiTitle: 'Workload Analyzer',
      });
    }
  });

  it('ignores unknown fields instead of failing', () => {
    const result = toChatBootstrap({ ...bootstrapConfig, unexpectedField: 'x' });

    expect(isOk(result)).toBe(true);
    if (isOk(result)) {
      expect(result.value).not.toHaveProperty('unexpectedField');
    }
  });

  it('accepts the backend-supported root /ui mount', () => {
    const result = toChatBootstrap({ ...bootstrapConfig, uiEndpoint: '/ui' });
    expect(isOk(result) && result.value.uiEndpoint).toBe('/ui');
  });

  it('rejects malformed UI mounts', () => {
    for (const uiEndpoint of ['//evil/ui', '/chat//ui', '/chat/other']) {
      expect(isErr(toChatBootstrap({ ...bootstrapConfig, uiEndpoint }))).toBe(true);
    }
  });

  it('translates the snake_case model-catalog fields to camelCase, including nested group models', () => {
    const result = toChatBootstrap(bootstrapConfig);

    expect(isOk(result)).toBe(true);
    if (isOk(result)) {
      expect(result.value.availableModels[0]).toEqual({
        id: 'us.anthropic.claude-sonnet-5',
        name: 'Claude Sonnet 5 (US)',
        provider: 'Anthropic',
        supportsTemperature: false,
        maxOutputTokens: 128000,
      });
      expect(result.value.availableModelGroups[0]?.models[0]).toEqual({
        id: 'us.anthropic.claude-sonnet-5',
        name: 'Claude Sonnet 5 (US)',
        provider: 'Anthropic',
        supportsTemperature: false,
        maxOutputTokens: 128000,
      });
    }
  });

  it('preserves optional generic prompt grouping metadata from the host application', () => {
    const result = toChatBootstrap({
      ...bootstrapConfig,
      presetPrompts: [{
        id: 'analysis-a',
        label: 'Analysis - A',
        group: 'Analysis',
        description: 'Analyze A',
        template: 'Analyze {{JOB_ID}}',
      }],
    });

    expect(isOk(result) && result.value.presetPrompts[0]).toMatchObject({
      group: 'Analysis',
    });
  });

  it('returns err for a payload missing a required field', () => {
    const incomplete: Record<string, unknown> = { ...bootstrapConfig };
    delete incomplete.websocketUrl;
    const result = toChatBootstrap(incomplete);

    expect(isErr(result)).toBe(true);
    if (isErr(result)) {
      expect(result.error.code).toBe('invalid-response');
      expect(result.error.issues.some((issue) => issue.startsWith('websocketUrl'))).toBe(true);
    }
  });

  it('defaults auth expiration behaviour to none when an older bootstrap omits it', () => {
    const legacyConfig = { ...bootstrapConfig };
    delete legacyConfig.authExpirationBehaviour;
    const result = toChatBootstrap(legacyConfig);

    expect(isOk(result) && result.value.authExpirationBehaviour).toBe('none');
  });

  it('defaults the SSO session renewal interval to one hour when an older bootstrap omits it', () => {
    const legacyConfig = { ...bootstrapConfig };
    delete legacyConfig.ssoSessionRenewalIntervalSeconds;
    const result = toChatBootstrap(legacyConfig);

    expect(isOk(result) && result.value.ssoSessionRenewalIntervalSeconds).toBe(3600);
  });

  it.each([0, -5, 1.5, 2_147_484])('rejects an invalid SSO session renewal interval (%s)', (interval) => {
    const result = toChatBootstrap({ ...bootstrapConfig, ssoSessionRenewalIntervalSeconds: interval });

    expect(isErr(result)).toBe(true);
  });

  it('returns err (never throws) for a completely malformed payload', () => {
    expect(() => toChatBootstrap('not an object')).not.toThrow();
    expect(isErr(toChatBootstrap('not an object'))).toBe(true);
    expect(isErr(toChatBootstrap(null))).toBe(true);
    expect(isErr(toChatBootstrap(undefined))).toBe(true);
  });
});
