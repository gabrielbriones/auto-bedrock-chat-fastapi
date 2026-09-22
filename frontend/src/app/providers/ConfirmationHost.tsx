import { useSyncExternalStore } from 'react'

import type { ConfirmationController } from '@/app/adapters/confirmation-controller'
import { ConfirmDialog } from '@/components/ui/composed/confirm-dialog'
import { PromptDialog } from '@/components/ui/composed/prompt-dialog'

export type ConfirmationHostProps = {
  readonly controller: ConfirmationController
}

// SPEC-021 §5: the single global host. Concurrent requests queue and are answered newest first
// (FR-SHELL-005, LIFO); each resolves its own promise (FR-SHELL-020). Only the top request is
// mounted, because two sibling modal roots would each mark the other inert.
export function ConfirmationHost({ controller }: ConfirmationHostProps) {
  const pending = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const top = pending.at(-1)

  if (top === undefined) {
    return null
  }

  return top.kind === 'confirm' ? (
    <ConfirmDialog
      key={top.id}
      request={top.request}
      onResolve={(confirmed) => {
        controller.settle(top.id, confirmed)
      }}
    />
  ) : (
    <PromptDialog
      key={top.id}
      request={top.request}
      onResolve={(value) => {
        controller.settle(top.id, value)
      }}
    />
  )
}
