import { useSyncExternalStore } from 'react'

import type { ConnectivityPort } from '@/shared/ports/connectivity-port'
import { SHELL } from '@/shared/copy/shell'

export type OfflineBannerProps = {
  readonly connectivity: ConnectivityPort
}

// FR-SHELL-022: persistent while offline, announced politely, and gone the moment connectivity
// returns — the socket suspends its backoff off the same port.
export function OfflineBanner({ connectivity }: OfflineBannerProps) {
  const status = useSyncExternalStore(
    (listener) => connectivity.subscribe(listener),
    () => connectivity.status(),
  )

  if (status === 'online') {
    return null
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-0 top-0 z-50 bg-warning-500 px-4 py-2 text-center text-sm text-neutral-950"
    >
      {SHELL.offline}
    </div>
  )
}
