import { NO_CAPABILITIES, type Capabilities } from '@/domains/iam/domain/capabilities'
import { IAM_COPY } from '@/shared/copy/iam'
import type { AuthPolicy, SessionAuthState } from '@/domains/iam/domain/auth-policy'
import type { CredentialKind } from '@/domains/iam/domain/credential'
import type { Principal } from '@/domains/iam/domain/principal'
import type { AuthEvent } from '@/domains/iam/application/ports'

export type AuthNotice = {
  readonly tone: 'success' | 'error' | 'warning'
  readonly text: string
}

export type AuthSession = {
  readonly status: SessionAuthState
  readonly principal: Principal
  readonly dialogOpen: boolean
  /** Pre-selects a kind when the dialog re-opens (FR-IAM-009 sends expiry back to `sso`). */
  readonly preselectedKind: CredentialKind | null
  readonly notice: AuthNotice | null
  /** FR-IAM-014/015: composing a message is blocked until auth resolves, unless it is optional. */
  readonly inputEnabled: boolean
}

export type AuthSessionAction =
  | { readonly type: 'submitted' }
  | { readonly type: 'send-failed' }
  | { readonly type: 'skipped' }
  | { readonly type: 'dialog-requested'; readonly kind?: CredentialKind }
  | { readonly type: 'socket-reset'; readonly reauthenticating: boolean }
  | { readonly type: 'auth-event'; readonly event: AuthEvent }

export type InitialSessionInput = {
  readonly policy: AuthPolicy
  readonly ssoAuthenticated: boolean
  readonly ssoUserDisplay: string | null
  readonly capabilities?: Capabilities
}

const anonymous = (capabilities: Capabilities): Principal => ({
  userId: null,
  displayName: null,
  mode: 'anonymous',
  capabilities,
})

// FR-IAM-017: an established SSO cookie authenticates during the handshake, so no dialog is shown.
export const initialAuthSession = ({
  policy,
  ssoAuthenticated,
  ssoUserDisplay,
  capabilities = NO_CAPABILITIES,
}: InitialSessionInput): AuthSession => {
  if (!policy.enabled || ssoAuthenticated) {
    return {
      status: ssoAuthenticated ? 'authenticated' : 'unauthenticated',
      principal: ssoAuthenticated
        ? { userId: null, displayName: ssoUserDisplay, mode: 'sso', capabilities }
        : anonymous(capabilities),
      dialogOpen: false,
      preselectedKind: null,
      notice: null,
      inputEnabled: true,
    }
  }

  return {
    status: 'unauthenticated',
    principal: anonymous(capabilities),
    dialogOpen: true,
    preselectedKind: null,
    notice: null,
    inputEnabled: !policy.required,
  }
}

// FR-IAM-014
const onConfigured = (
  session: AuthSession,
  event: Extract<AuthEvent, { kind: 'configured' }>,
): AuthSession => ({
  ...session,
  status: 'authenticated',
  principal: {
    ...session.principal,
    displayName: event.displayName ?? session.principal.displayName,
    mode: event.authKind === 'sso' ? 'sso' : 'tool-auth',
  },
  dialogOpen: false,
  preselectedKind: null,
  notice: { tone: 'success', text: `🔐 ${event.message}` },
  inputEnabled: true,
})

// FR-IAM-015 — the credential itself is discarded by the store, not here.
const onFailed = (
  session: AuthSession,
  policy: AuthPolicy,
  event: Extract<AuthEvent, { kind: 'failed' }>,
): AuthSession => ({
  ...session,
  status: 'failed',
  dialogOpen: true,
  preselectedKind: event.authKind,
  notice: { tone: 'error', text: `❌ Authentication failed: ${event.message}` },
  inputEnabled: !policy.required,
})

// FR-IAM-009
const onExpired = (
  session: AuthSession,
  policy: AuthPolicy,
  event: Extract<AuthEvent, { kind: 'expired' }>,
): AuthSession => ({
  status: 'expired',
  principal: anonymous(session.principal.capabilities),
  dialogOpen: true,
  preselectedKind: policy.ssoEnabled ? 'sso' : null,
  notice: {
    tone: 'warning',
    text: `⏰ ${event.message === '' ? 'Session expired. Please log in again.' : event.message}`,
  },
  inputEnabled: false,
})

const onLoggedOut = (
  session: AuthSession,
  policy: AuthPolicy,
  event: Extract<AuthEvent, { kind: 'logged-out' }>,
): AuthSession => ({
  status: 'unauthenticated',
  principal: anonymous(session.principal.capabilities),
  dialogOpen: policy.required,
  preselectedKind: null,
  notice: { tone: 'success', text: event.message },
  inputEnabled: !policy.required,
})

const onAuthEvent = (session: AuthSession, policy: AuthPolicy, event: AuthEvent): AuthSession => {
  switch (event.kind) {
    case 'configured':
      return onConfigured(session, event)
    case 'failed':
      return onFailed(session, policy, event)
    case 'expired':
      return onExpired(session, policy, event)
    case 'logged-out':
      return onLoggedOut(session, policy, event)
  }
}

export const authSessionReducer = (
  session: AuthSession,
  policy: AuthPolicy,
  action: AuthSessionAction,
): AuthSession => {
  switch (action.type) {
    case 'submitted':
      return { ...session, status: 'authenticating', notice: null }
    // FR-IAM-010b: a frame that never left restores the control rather than waiting for a reply
    // that was never requested. The credential is kept for replay on the next open socket.
    case 'send-failed':
      return {
        ...session,
        status: 'unauthenticated',
        dialogOpen: true,
        notice: { tone: 'warning', text: IAM_COPY.errors.notConnected },
      }
    // FR-IAM-008: skipping is only reachable when auth is optional.
    case 'skipped':
      return policy.required ? session : { ...session, dialogOpen: false, inputEnabled: true }
    case 'dialog-requested':
      return {
        ...session,
        dialogOpen: true,
        preselectedKind: action.kind ?? session.preselectedKind,
      }
    // FR-IAM-010: a new socket is a new session. A stored credential is replayed without asking.
    case 'socket-reset':
      return {
        ...session,
        status: action.reauthenticating ? 'authenticating' : 'unauthenticated',
        dialogOpen: session.dialogOpen && !action.reauthenticating,
      }
    case 'auth-event':
      return onAuthEvent(session, policy, action.event)
  }
}
