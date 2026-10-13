import type {
  ConnectivityPort,
  ConnectivityStatus,
} from '@/shared/ports/connectivity-port'

// STD-002 §4: drive offline behaviour from a test instead of from real browser events.
export class FakeConnectivityPort implements ConnectivityPort {
  #status: ConnectivityStatus
  readonly #listeners = new Set<(status: ConnectivityStatus) => void>()

  constructor(initial: ConnectivityStatus = 'online') {
    this.#status = initial
  }

  status(): ConnectivityStatus {
    return this.#status
  }

  subscribe(listener: (status: ConnectivityStatus) => void): () => void {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  set(status: ConnectivityStatus): void {
    this.#status = status

    for (const listener of this.#listeners) {
      listener(status)
    }
  }
}
