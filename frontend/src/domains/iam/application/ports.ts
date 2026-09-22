import type { Problem } from '@/shared/http/exception'
import type { Result } from '@/shared/kernel/result'
import type { SendResult } from '@/shared/ws/socket-client'

import type { Capabilities } from '@/domains/iam/domain/capabilities'
import type { Credential, CredentialKind } from '@/domains/iam/domain/credential'

export type AuthEvent =
  | {
      readonly kind: 'configured'
      readonly authKind: CredentialKind
      readonly displayName?: string
      readonly message: string
    }
  | {
      readonly kind: 'failed'
      readonly authKind: CredentialKind
      readonly message: string
      readonly redirectUrl?: string
    }
  | {
      readonly kind: 'expired'
      readonly message: string
      readonly redirectUrl?: string
    }
  | {
      readonly kind: 'logged-out'
      readonly message: string
    }

export interface AuthGateway {
  // FR-IAM-010b: the caller must know a frame was dropped, or the UI waits forever for a reply
  // that was never requested.
  authenticate(credential: Credential): SendResult
  logout(): SendResult
  onAuthEvent(listener: (event: AuthEvent) => void): () => void
}

export interface SsoGateway {
  beginLogin(returnTo: string): void
  logout(): Promise<Result<void, Problem>>
}

export interface CapabilityProbe {
  probe(): Promise<Capabilities>
  invalidate(): void
}