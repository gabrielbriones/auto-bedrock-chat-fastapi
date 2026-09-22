import type {
  ConnectivityPort,
  ConnectivityStatus,
} from '@/shared/ports/connectivity-port'

// FR-SHELL-022: `navigator.onLine` alone is unreliable as a poll, but its transitions are exactly
// what the browser reports through these two events.
export class BrowserConnectivityPort implements ConnectivityPort {
  status(): ConnectivityStatus {
    return navigator.onLine ? 'online' : 'offline'
  }

  subscribe(listener: (status: ConnectivityStatus) => void): () => void {
    const onOnline = () => {
      listener('online')
    }
    const onOffline = () => {
      listener('offline')
    }

    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)

    return () => {
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
    }
  }
}
