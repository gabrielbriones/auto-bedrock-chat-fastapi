import type {
  ConfirmRequest,
  ConfirmationPort,
  PromptRequest,
} from '@/shared/ports/confirmation-port'

export type PendingConfirmation =
  | {
      readonly id: string
      readonly kind: 'confirm'
      readonly request: ConfirmRequest
    }
  | {
      readonly id: string
      readonly kind: 'prompt'
      readonly request: PromptRequest
    }

type Resolver = (value: never) => void

// SPEC-021 §5. FR-SHELL-020: each request keeps its own resolver, so two confirmations open at
// once settle independently — the legacy single-resolver design orphaned the first promise.
// Framework-free on purpose: use cases call it, and the React host only subscribes.
export class ConfirmationController implements ConfirmationPort {
  #pending: readonly PendingConfirmation[] = []
  readonly #resolvers = new Map<string, Resolver>()
  readonly #listeners = new Set<() => void>()
  #nextId = 0

  confirm(request: ConfirmRequest): Promise<boolean> {
    return this.#enqueue<boolean>({ kind: 'confirm', request })
  }

  prompt(request: PromptRequest): Promise<string | null> {
    return this.#enqueue<string | null>({ kind: 'prompt', request })
  }

  // Settles one request; anything already resolved is ignored, so a double dismissal is harmless.
  settle(id: string, value: boolean | string | null): void {
    const resolve = this.#resolvers.get(id)

    if (resolve === undefined) {
      return
    }

    this.#resolvers.delete(id)
    this.#pending = this.#pending.filter((item) => item.id !== id)
    ;(resolve as (value: boolean | string | null) => void)(value)
    this.#emit()
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  // Identity-stable between emissions, as useSyncExternalStore requires.
  getSnapshot = (): readonly PendingConfirmation[] => this.#pending

  #enqueue<T extends boolean | string | null>(
    item: { kind: 'confirm'; request: ConfirmRequest } | { kind: 'prompt'; request: PromptRequest },
  ): Promise<T> {
    const id = `confirmation-${(this.#nextId += 1)}`

    return new Promise<T>((resolve) => {
      this.#resolvers.set(id, resolve as Resolver)
      this.#pending = [...this.#pending, { id, ...item }]
      this.#emit()
    })
  }

  #emit(): void {
    for (const listener of this.#listeners) {
      listener()
    }
  }
}
