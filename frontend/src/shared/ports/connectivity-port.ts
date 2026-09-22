export type ConnectivityStatus = 'online' | 'offline'

// FR-SHELL-022. A port because the socket (T-050) must suspend its backoff while offline and
// reconnect the moment connectivity returns, and neither behaviour is testable against real
// browser events.
export interface ConnectivityPort {
  status(): ConnectivityStatus
  subscribe(listener: (status: ConnectivityStatus) => void): () => void
}
