import type { ConnectionStatus } from '@/shared/ws/socket-client'

// FR-MSG-002. The domain decides *what* is being reported; `shared/copy/messaging` decides how it
// reads, so the three states stay translatable and testable without rendering anything.
export type ConnectionView =
  | { readonly kind: 'connected' }
  | { readonly kind: 'connecting' }
  | { readonly kind: 'disconnected' }

// `idle` is the state before the composition root has called `connect()`. It is reported as
// connecting because the provider connects on mount and the user never has a choice in between.
// A dropped socket is reported as `disconnected` immediately: the retry loop happening behind the
// scenes is not surfaced as its own state, so the user never sees an intermediate "reconnecting".
export const connectionView = (status: ConnectionStatus): ConnectionView => {
  switch (status) {
    case 'open':
      return { kind: 'connected' }
    case 'idle':
    case 'connecting':
      return { kind: 'connecting' }
    case 'reconnecting':
    case 'closed':
      return { kind: 'disconnected' }
  }
}
