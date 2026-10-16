import type { ConnectionState, Unsubscribe } from '@/shared/ws/socket-client'

import type { AuthPolicy } from '@/domains/iam/domain/auth-policy'
import type { Credential } from '@/domains/iam/domain/credential'
import type {
  AuthGateway,
  Cancel,
  SessionRenewalScheduler,
  SsoGateway,
} from '@/domains/iam/application/ports'
import { applyAuthMethodDeepLink } from '@/domains/iam/application/auth-deep-link'
import {
  authSessionReducer,
  initialAuthSession,
  shouldRenewSession,
  type AuthSession,
  type AuthSessionAction,
  type InitialSessionInput,
} from '@/domains/iam/application/auth-session'

export interface ConnectionSource {
  onStateChange(listener: (state: ConnectionState) => void): Unsubscribe
}

export type IdentityStoreOptions = {
  readonly policy: AuthPolicy
  readonly authGateway: AuthGateway
  readonly ssoGateway: SsoGateway
  readonly connection: ConnectionSource
  readonly renewalScheduler: SessionRenewalScheduler
  readonly renewalIntervalMs: number
  readonly initial: InitialSessionInput
}

// The single owner of the live credential. It is held in memory only: never persisted, never
// logged, and never handed back out (SPEC-010 §1).
export class IdentityStore {
  readonly #authGateway: AuthGateway
  #credential: Credential | null = null
  readonly #listeners = new Set<() => void>()
  readonly #policy: AuthPolicy
  #cancelRenewal: Cancel | null = null
  // Set by logout: a closed socket never delivers `logout_success`, so the principal stays `sso`.
  #renewalHeld = false
  readonly #renewalIntervalMs: number
  readonly #renewalScheduler: SessionRenewalScheduler
  #session: AuthSession
  // XMGPLAT-11999: an SSO principal is re-verified from the cookie during the handshake.
  #ssoHandshakePending = false
  readonly #ssoGateway: SsoGateway
  #subscriptions: readonly Unsubscribe[] = []
  #wasOpen = false

  constructor(options: IdentityStoreOptions) {
    this.#policy = options.policy
    this.#authGateway = options.authGateway
    this.#ssoGateway = options.ssoGateway
    this.#renewalScheduler = options.renewalScheduler
    this.#renewalIntervalMs = options.renewalIntervalMs
    this.#session = initialAuthSession(options.initial)

    this.#subscriptions = [
      options.authGateway.onAuthEvent((event) => {
        if (event.kind === 'failed' || event.kind === 'expired' || event.kind === 'logged-out') {
          this.#credential = null
        }
        if (event.kind === 'configured') {
          this.#renewalHeld = false
          this.#ssoHandshakePending = false
        }
        if (event.kind === 'handshake-complete') {
          this.#settleSsoHandshake()
        }
        this.#dispatch({ type: 'auth-event', event })
      }),
      options.connection.onStateChange((state) => {
        this.#handleConnectionState(state)
      }),
    ]

    this.#syncRenewal()
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  // Identity-stable between emissions, as useSyncExternalStore requires.
  getSnapshot = (): AuthSession => this.#session

  submit(credential: Credential): void {
    this.#credential = credential
    this.#dispatch({ type: 'submitted' })

    if (this.#authGateway.authenticate(credential) === 'dropped-closed') {
      this.#dispatch({ type: 'send-failed' })
    }
  }

  // FR-IAM-007: applied once at startup. Never reads a credential value from the URL.
  applyDeepLink(rawAuthMethod: string | null | undefined, returnTo: string): void {
    if (!this.#session.dialogOpen) {
      return
    }

    const outcome = applyAuthMethodDeepLink(rawAuthMethod, this.#policy)

    if (outcome.kind === 'auto-redirect') {
      this.#ssoGateway.beginLogin(returnTo)
      return
    }

    if (outcome.kind === 'preselect') {
      this.#dispatch({ type: 'dialog-requested', kind: outcome.credentialKind })
    }
  }

  skip(): void {
    this.#dispatch({ type: 'skipped' })
  }

  openDialog(kind?: Credential['kind']): void {
    this.#dispatch(kind === undefined ? { type: 'dialog-requested' } : { type: 'dialog-requested', kind })
  }

  beginSsoLogin(returnTo: string): void {
    this.#ssoGateway.beginLogin(returnTo)
  }

  // FR-IAM-006 / FR-IAM-006a: logout clears any stored credential and requests backend logout;
  // SSO additionally posts to the cookie logout endpoint and surfaces HTTP failures to the caller.
  async logout(): Promise<void> {
    this.#credential = null
    this.#renewalHeld = true
    this.#stopRenewal()
    this.#authGateway.logout()

    if (this.#session.principal.mode !== 'sso') {
      return
    }

    const result = await this.#ssoGateway.logout()
    if (result.kind === 'err') {
      throw new Error(result.error.title)
    }
  }

  dispose(): void {
    this.#stopRenewal()
    for (const unsubscribe of this.#subscriptions) {
      unsubscribe()
    }
    this.#subscriptions = []
    this.#listeners.clear()
  }

  // FR-IAM-010: authentication is per socket, so every fresh `open` re-sends the stored credential
  // before anything else is permitted to use the connection.
  #handleConnectionState(state: ConnectionState): void {
    const isOpen = state.status === 'open'

    if (isOpen === this.#wasOpen) {
      return
    }

    this.#wasOpen = isOpen

    if (!isOpen) {
      return
    }

    const credential = this.#credential
    // Dropping an SSO principal to `unauthenticated` here hid the conversations on every
    // reconnect, which wiped the transcript; the server confirms or denies it before
    // `connection_established`. A socket that drops before then leaves the check pending, so it
    // carries over to the next attempt instead of being read as a sign-out.
    const sso = credential === null && this.#session.principal.mode === 'sso'
    this.#ssoHandshakePending =
      sso &&
      (this.#session.status === 'authenticated' ||
        (this.#session.status === 'authenticating' && this.#ssoHandshakePending))
    this.#dispatch({
      type: 'socket-reset',
      reauthenticating: credential !== null || this.#ssoHandshakePending,
    })

    if (credential !== null && this.#authGateway.authenticate(credential) === 'dropped-closed') {
      this.#dispatch({ type: 'send-failed' })
    }
  }

  // The handshake ended without `auth_configured`: the cookie was not accepted.
  #settleSsoHandshake(): void {
    if (!this.#ssoHandshakePending) {
      return
    }

    this.#ssoHandshakePending = false
    this.#dispatch({ type: 'socket-reset', reauthenticating: false })
  }

  #dispatch(action: AuthSessionAction): void {
    const next = authSessionReducer(this.#session, this.#policy, action)

    if (next === this.#session) {
      return
    }

    this.#session = next
    this.#syncRenewal()
    for (const listener of this.#listeners) {
      listener()
    }
  }

  // Idempotent, so a repeated `auth_configured` (every reconnect) never stacks a second timer.
  #syncRenewal(): void {
    const shouldRun = !this.#renewalHeld && shouldRenewSession(this.#policy, this.#session)

    if (shouldRun && this.#cancelRenewal === null) {
      this.#cancelRenewal = this.#renewalScheduler.every(this.#renewalIntervalMs, () => {
        this.#renew()
      })
      this.#renew()
    } else if (!shouldRun) {
      this.#stopRenewal()
    }
  }

  #stopRenewal(): void {
    this.#cancelRenewal?.()
    this.#cancelRenewal = null
  }

  #renew(): void {
    const renewal = this.#cancelRenewal
    void this.#ssoGateway.refresh().then((outcome) => {
      // Ignore a result from a renewal lifecycle that has since stopped or restarted.
      if (outcome !== 'expired' || renewal === null || this.#cancelRenewal !== renewal) {
        return
      }
      this.#credential = null
      this.#dispatch({ type: 'auth-event', event: { kind: 'expired', message: '' } })
    })
    this.#authGateway.refreshSessionToken()
  }
}
