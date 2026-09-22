import { describe, expect, it } from 'vitest'

import { IAM_COPY } from '@/shared/copy/iam'

import { validateCredential } from '@/domains/iam/domain/credential-policy'

const credentialValue = 'fixture-credential'
const paddedCredentialValue = ` ${credentialValue} `

describe('validateCredential', () => {
  it('validates and trims each field-based credential kind', () => {
    expect(validateCredential('bearer_token', { token: ' token ' })).toEqual({
      kind: 'ok',
      value: { kind: 'bearer_token', token: 'token' },
    })
    expect(validateCredential('basic_auth', { username: ' user ', password: paddedCredentialValue })).toEqual({
      kind: 'ok',
      value: { kind: 'basic_auth', username: 'user', password: credentialValue },
    })
    expect(validateCredential('api_key', { apiKey: ' key ', header: ' X-API-Key ' })).toEqual({
      kind: 'ok',
      value: { kind: 'api_key', apiKey: 'key', header: 'X-API-Key' },
    })
    expect(validateCredential('oauth2_client_credentials', {
      clientId: ' client ',
      clientSecret: ` ${credentialValue} `,
      tokenUrl: ' https://issuer.test/token ',
      scope: ' read ',
    })).toEqual({
      kind: 'ok',
      value: {
        kind: 'oauth2_client_credentials',
        clientId: 'client',
        clientSecret: credentialValue,
        tokenUrl: 'https://issuer.test/token',
        scope: 'read',
      },
    })
  })

  it('returns every missing required field without throwing', () => {
    expect(validateCredential('bearer_token', {})).toEqual({
      kind: 'err',
      error: { token: IAM_COPY.validation.required },
    })
    expect(validateCredential('basic_auth', { username: 42 })).toEqual({
      kind: 'err',
      error: {
        username: IAM_COPY.validation.required,
        password: IAM_COPY.validation.required,
      },
    })
    expect(validateCredential('api_key', {})).toEqual({
      kind: 'err',
      error: {
        apiKey: IAM_COPY.validation.required,
        header: IAM_COPY.validation.required,
      },
    })
    expect(validateCredential('oauth2_client_credentials', {})).toEqual({
      kind: 'err',
      error: {
        clientId: IAM_COPY.validation.required,
        clientSecret: IAM_COPY.validation.required,
        tokenUrl: IAM_COPY.validation.required,
      },
    })
  })

  it('omits an empty optional OAuth scope', () => {
    expect(validateCredential('oauth2_client_credentials', {
      clientId: 'client',
      clientSecret: credentialValue,
      tokenUrl: 'https://issuer.test/token',
      scope: '   ',
    })).toEqual({
      kind: 'ok',
      value: {
        kind: 'oauth2_client_credentials',
        clientId: 'client',
        clientSecret: credentialValue,
        tokenUrl: 'https://issuer.test/token',
      },
    })
  })

  it('parses custom headers with all-string values', () => {
    expect(validateCredential('custom', { headers: `{"X-API-Key":"${credentialValue}"}` })).toEqual({
      kind: 'ok',
      value: { kind: 'custom', headers: { 'X-API-Key': credentialValue } },
    })
  })

  it.each([
    ['', IAM_COPY.validation.required],
    ['{not json', IAM_COPY.validation.invalidJson],
    ['null', IAM_COPY.validation.invalidHeaderMap],
    ['["value"]', IAM_COPY.validation.invalidHeaderMap],
    ['{"X-A":5}', IAM_COPY.validation.invalidHeaderMap],
  ])('distinguishes invalid custom headers %#', (headers, message) => {
    expect(validateCredential('custom', { headers })).toEqual({
      kind: 'err',
      error: { headers: message },
    })
  })

  it('accepts SSO without a credential value', () => {
    expect(validateCredential('sso', { token: 'ignored' })).toEqual({
      kind: 'ok',
      value: { kind: 'sso' },
    })
  })
})