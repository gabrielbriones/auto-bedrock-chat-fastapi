import { createContext, useContext, useSyncExternalStore } from 'react'

import type { Container } from '@/app/bootstrap/container'

// Set once, at the root, by BootstrapProvider after the container is built (ADR-003: no
// service locator) — `undefined` here only ever means "rendered outside the provider".
export const ContainerContext = createContext<Container | undefined>(undefined)

export const useContainer = (): Container => {
  const container = useContext(ContainerContext)

  if (container === undefined) {
    throw new Error('useContainer must be used within a BootstrapProvider')
  }

  return container
}

type ExternalStore<S> = {
  readonly subscribe: (listener: () => void) => () => void
  readonly getSnapshot: () => S
}

export type ContainerStoreKey = {
  [K in keyof Container]: Container[K] extends ExternalStore<unknown> ? K : never
}[keyof Container]

export type ContainerSnapshot<K extends ContainerStoreKey> =
  Container[K] extends ExternalStore<infer S> ? S : never

// Subscribes a component to one of the container's external stores by key.
export const useContainerStore = <K extends ContainerStoreKey>(key: K): ContainerSnapshot<K> => {
  // TS cannot relate `Container[K]` to the inferred snapshot for a generic `K`; the key type
  // already guarantees the store shape.
  const store = useContainer()[key] as unknown as ExternalStore<ContainerSnapshot<K>>

  return useSyncExternalStore(store.subscribe, store.getSnapshot)
}
