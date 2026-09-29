import type { UserId } from '@/shared/kernel/branded'

import type { Capabilities } from '@/domains/iam/domain/capabilities'

export type AuthMode = 'sso' | 'tool-auth' | 'anonymous'

export type Principal = {
  readonly userId: UserId | null
  readonly displayName: string | null
  readonly mode: AuthMode
  readonly capabilities: Capabilities
}