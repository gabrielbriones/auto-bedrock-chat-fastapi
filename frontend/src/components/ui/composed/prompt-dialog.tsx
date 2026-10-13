import { useId, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { SHELL } from '@/shared/copy/shell'
import type { PromptRequest } from '@/shared/ports/confirmation-port'

export type PromptDialogProps = {
  readonly request: PromptRequest
  readonly onResolve: (value: string | null) => void
}

// SPEC-020 §3.2: replaces `window.prompt`. Cancelling resolves null; an empty submission is only
// accepted in the optional mode the rollback reason uses (FR-SHELL-021).
export function PromptDialog({ request, onResolve }: PromptDialogProps) {
  const [value, setValue] = useState(request.initialValue ?? '')
  const inputId = useId()
  const canSubmit = request.optional === true || value.trim().length > 0

  const submit = (event: FormEvent) => {
    event.preventDefault()

    if (canSubmit) {
      onResolve(value)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) {
          onResolve(null)
        }
      }}
    >
      <DialogContent showCloseButton={false}>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{request.title}</DialogTitle>
            {request.message !== undefined ? (
              <DialogDescription>{request.message}</DialogDescription>
            ) : null}
          </DialogHeader>

          <div className="grid gap-2">
            <Label htmlFor={inputId}>{request.label}</Label>
            <Input
              id={inputId}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onResolve(null)}>
              {SHELL.confirmations.cancel}
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {request.confirmLabel ?? SHELL.confirmations.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
