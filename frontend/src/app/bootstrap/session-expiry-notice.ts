import { IAM_COPY } from '@/shared/copy/iam'
import type { NotificationPort } from '@/shared/ports/notification-port'

import type { IdentityStore } from '@/domains/iam/application/identity.store'

// Persistent, so the user knows why the login dialog reappeared.
export const notifyOnSessionExpiry = (identity: IdentityStore, notifications: NotificationPort): void => {
  let status = identity.getSnapshot().status
  identity.subscribe(() => {
    const next = identity.getSnapshot().status
    if (next === 'expired' && status !== 'expired') {
      notifications.warning(IAM_COPY.sessionExpired.title, {
        description: IAM_COPY.sessionExpired.description,
        durationMs: 0,
      })
    }
    status = next
  })
}
