import { useRef } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { SHELL } from '@/shared/copy/shell'
import type { ConfirmRequest } from '@/shared/ports/confirmation-port'

export type ConfirmDialogProps = {
  readonly request: ConfirmRequest
  readonly onResolve: (confirmed: boolean) => void
}

// SPEC-020 §3.2 / FR-SHELL-008: destructive confirmations are toned as such and never open with
// the confirm action focused. Focus trapping, Escape and focus restoration come from Base UI
// (FR-DS-023 — none of it is hand-rolled here).
export function ConfirmDialog({ request, onResolve }: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) {
          onResolve(false)
        }
      }}
    >
      <DialogContent showCloseButton={false} initialFocus={cancelRef}>
        <DialogHeader>
          <DialogTitle>{request.title}</DialogTitle>
          <DialogDescription>{request.message}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button ref={cancelRef} type="button" variant="outline" onClick={() => onResolve(false)}>
            {SHELL.confirmations.cancel}
          </Button>
          <Button
            type="button"
            variant={request.tone === 'destructive' ? 'destructive' : 'default'}
            onClick={() => onResolve(true)}
          >
            {request.confirmLabel ?? SHELL.confirmations.confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
