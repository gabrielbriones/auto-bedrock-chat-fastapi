import { describe, expect, it, jest } from '@jest/globals'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'

import { IAM_COPY } from '@/shared/copy/iam'

import { toAuthPolicy, type AuthPolicySource } from '@/domains/iam/domain/auth-policy'
import type { Credential } from '@/domains/iam/domain/credential'
import { AuthDialog, type AuthDialogProps } from '@/domains/iam/presentation/AuthDialog'

const policySource = (overrides: Partial<AuthPolicySource> = {}): AuthPolicySource => ({
  authEnabled: true,
  requireAuth: false,
  supportedAuthTypes: [
    'bearer_token',
    'basic_auth',
    'api_key',
    'oauth2',
    'custom',
    'sso',
  ],
  defaultAuthType: 'bearer_token',
  ssoEnabled: true,
  ssoLoginUrl: '/chat/auth/sso/login',
  ...overrides,
})

const renderDialog = (
  overrides: Partial<AuthDialogProps> = {},
  source: Partial<AuthPolicySource> = {},
) => {
  const onSubmit = jest.fn<(credential: Credential) => void>()
  const onSkip = jest.fn()
  const onSsoLogin = jest.fn()

  const { unmount } = render(
    <AuthDialog
      policy={toAuthPolicy(policySource(source))}
      open
      submitting={false}
      onSubmit={onSubmit}
      onSkip={onSkip}
      onSsoLogin={onSsoLogin}
      {...overrides}
    />,
  )

  return { onSubmit, onSkip, onSsoLogin, unmount, user: userEvent.setup() }
}

const selectKind = async (user: ReturnType<typeof userEvent.setup>, label: string) => {
  await user.click(screen.getByRole('combobox', { name: IAM_COPY.dialog.kindLabel }))
  await user.click(await screen.findByRole('option', { name: label }))
}

const credentialValue = 'fixture-credential'

describe('the auth dialog', () => {
  // FR-IAM-001 / FR-IAM-001b
  it('lists every supported kind and pre-selects the default', async () => {
    const { user } = renderDialog()

    expect(screen.getByLabelText(IAM_COPY.fields.token.label)).toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: IAM_COPY.dialog.kindLabel }))
    const options = (await screen.findAllByRole('option')).map((option) => option.textContent)

    expect(options).toEqual([
      IAM_COPY.kinds.bearer_token,
      IAM_COPY.kinds.basic_auth,
      IAM_COPY.kinds.api_key,
      IAM_COPY.kinds.oauth2_client_credentials,
      IAM_COPY.kinds.custom,
      IAM_COPY.kinds.sso,
    ])
  })

  // FR-IAM-001a
  it('hides the selector and auto-selects when only one kind survives filtering', () => {
    renderDialog({}, { supportedAuthTypes: ['sso', 'basic_auth'], ssoEnabled: false })

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.getByLabelText(IAM_COPY.fields.username.label)).toBeInTheDocument()
  })

  // FR-IAM-003 — the leak-prevention requirement.
  it('clears the previous credential when the kind changes', async () => {
    const { user } = renderDialog({}, { defaultAuthType: 'basic_auth' })

    await user.type(screen.getByLabelText(IAM_COPY.fields.username.label), 'ada')
    await user.type(screen.getByLabelText(IAM_COPY.fields.password.label), credentialValue)

    await selectKind(user, IAM_COPY.kinds.api_key)

    expect(document.body.textContent).not.toContain(credentialValue)
    expect(screen.getByLabelText(IAM_COPY.fields.apiKey.label)).toHaveValue('')

    await selectKind(user, IAM_COPY.kinds.basic_auth)

    expect(screen.getByLabelText(IAM_COPY.fields.username.label)).toHaveValue('')
    expect(screen.getByLabelText(IAM_COPY.fields.password.label)).toHaveValue('')
  })

  it('resets the api key header to its default rather than blanking it', async () => {
    const { user } = renderDialog({}, { defaultAuthType: 'api_key' })

    await user.clear(screen.getByLabelText(IAM_COPY.fields.header.label))
    await user.type(screen.getByLabelText(IAM_COPY.fields.header.label), 'X-Other')
    await selectKind(user, IAM_COPY.kinds.custom)
    await selectKind(user, IAM_COPY.kinds.api_key)

    expect(screen.getByLabelText(IAM_COPY.fields.header.label)).toHaveValue('X-API-Key')
  })

  // FR-IAM-004
  it('marks invalid fields and moves focus to the first of them', async () => {
    const { user, onSubmit } = renderDialog({}, { defaultAuthType: 'basic_auth' })

    await user.type(screen.getByLabelText(IAM_COPY.fields.password.label), credentialValue)
    await user.click(screen.getByRole('button', { name: IAM_COPY.dialog.submit }))

    const username = screen.getByLabelText(IAM_COPY.fields.username.label)

    expect(onSubmit).not.toHaveBeenCalled()
    expect(username).toHaveAttribute('aria-invalid', 'true')
    expect(username).toHaveFocus()
    expect(screen.getByRole('alert')).toHaveTextContent(IAM_COPY.validation.required)
  })

  it('clears a field error as soon as that field is typed in', async () => {
    const { user } = renderDialog({}, { defaultAuthType: 'bearer_token' })

    await user.click(screen.getByRole('button', { name: IAM_COPY.dialog.submit }))
    expect(screen.getByRole('alert')).toBeInTheDocument()

    await user.type(screen.getByLabelText(IAM_COPY.fields.token.label), 't')

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('emits the validated credential on a good submission', async () => {
    const { user, onSubmit } = renderDialog({}, { defaultAuthType: 'bearer_token' })

    await user.type(screen.getByLabelText(IAM_COPY.fields.token.label), '  jwt-value  ')
    await user.click(screen.getByRole('button', { name: IAM_COPY.dialog.submit }))

    expect(onSubmit).toHaveBeenCalledWith({ kind: 'bearer_token', token: 'jwt-value' })
  })

  // SPEC-010 §6: the two custom-header errors stay distinguishable.
  it('distinguishes malformed JSON from a non-string header map', async () => {
    const { user } = renderDialog({}, { defaultAuthType: 'custom' })

    const headers = screen.getByLabelText(IAM_COPY.fields.headers.label)
    const submit = screen.getByRole('button', { name: IAM_COPY.dialog.submit })

    await user.type(headers, '{{not json')
    await user.click(submit)
    expect(screen.getByRole('alert')).toHaveTextContent(IAM_COPY.validation.invalidJson)

    await user.clear(headers)
    await user.type(headers, '{{"X-A": 5}')
    await user.click(submit)
    expect(screen.getByRole('alert')).toHaveTextContent(IAM_COPY.validation.invalidHeaderMap)
  })

  // FR-IAM-010b
  it('disables and relabels the submit control while a frame is in flight', () => {
    renderDialog({ submitting: true }, { defaultAuthType: 'bearer_token' })

    expect(screen.getByRole('button', { name: IAM_COPY.dialog.submitting })).toBeDisabled()
  })

  // FR-IAM-008 / FR-IAM-013
  it('offers Skip and Escape dismissal only when authentication is optional', async () => {
    const { user, onSkip } = renderDialog({}, { requireAuth: false })

    await user.click(screen.getByRole('button', { name: IAM_COPY.dialog.skip }))
    expect(onSkip).toHaveBeenCalledTimes(1)

    await user.keyboard('{Escape}')
    expect(onSkip).toHaveBeenCalledTimes(2)
  })

  it('offers no way out when authentication is required', async () => {
    const { user, onSkip } = renderDialog({}, { requireAuth: true })

    expect(screen.queryByRole('button', { name: IAM_COPY.dialog.skip })).not.toBeInTheDocument()

    await user.keyboard('{Escape}')

    expect(onSkip).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  // FR-IAM-005a
  it('replaces the submit control with an SSO redirect button that latches', async () => {
    const { user, onSsoLogin } = renderDialog({ preselectedKind: 'sso' })

    expect(screen.queryByRole('button', { name: IAM_COPY.dialog.submit })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: IAM_COPY.sso.login }))

    expect(onSsoLogin).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: IAM_COPY.sso.redirecting })).toBeDisabled()
  })

  // FR-IAM-013. jsdom implements neither `inert` nor the tab order, so the only honest assertion
  // here is that the application root is marked; the trap itself is proven in
  // tests/e2e/auth-dialog-keyboard.test.ts.
  it('marks the application root inert while open, and releases it on close', async () => {
    const appRoot = document.createElement('div')
    appRoot.id = 'root'
    document.body.append(appRoot)

    const { unmount } = renderDialog({}, { supportedAuthTypes: ['bearer_token'], requireAuth: true })

    const dialog = await screen.findByRole('dialog')

    expect(dialog).toHaveAccessibleName(IAM_COPY.dialog.title)
    expect(appRoot.hasAttribute('inert')).toBe(true)
    expect(document.querySelectorAll('[data-base-ui-focus-guard]').length).toBeGreaterThan(0)

    unmount()

    expect(appRoot.hasAttribute('inert')).toBe(false)

    appRoot.remove()
  })

  // NFR-A11Y-001
  it('has no axe violations', async () => {
    renderDialog({}, { defaultAuthType: 'oauth2' })

    const results = await axe(screen.getByRole('dialog'))

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
