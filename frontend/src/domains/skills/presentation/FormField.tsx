import { useId, type ReactNode } from 'react'

import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { SKILLS_COPY } from '@/shared/copy/skills'

export type ControlProps = {
  readonly id: string
  readonly 'aria-invalid'?: true
  readonly 'aria-describedby'?: string
}

export type FormFieldProps = {
  readonly label: string
  readonly hint?: string
  readonly error: string | null
  /** Shown as `used / max` and turns destructive past the limit; omitted while the limit is unknown. */
  readonly counter?: { readonly used: number; readonly max: number } | null
  readonly children: (control: ControlProps) => ReactNode
}

// One labelled control with its hint, live character counter and validation message, all linked
// through `aria-describedby` so a screen reader announces them with the field.
export function FormField({ label, hint, error, counter, children }: FormFieldProps) {
  const id = useId()
  const hintId = `${id}-hint`
  const counterId = `${id}-counter`
  const errorId = `${id}-error`
  const describedBy = [
    hint === undefined ? null : hintId,
    counter === undefined || counter === null ? null : counterId,
    error === null ? null : errorId,
  ].filter((value) => value !== null).join(' ')
  const over = counter !== undefined && counter !== null && counter.used > counter.max

  return (
    <div className="grid content-start gap-1.5">
      <div className="flex min-h-4 items-end justify-between gap-2">
        <Label htmlFor={id}>{label}</Label>
        {counter === undefined || counter === null ? null : (
          <span id={counterId} className={cn('text-xs tabular-nums text-muted-foreground', over && 'text-destructive')}>
            {SKILLS_COPY.editor.counter(counter.used, counter.max)}
          </span>
        )}
      </div>
      {children({
        id,
        ...(error === null ? {} : { 'aria-invalid': true as const }),
        ...(describedBy === '' ? {} : { 'aria-describedby': describedBy }),
      })}
      {hint === undefined ? null : <p id={hintId} className="text-xs text-muted-foreground">{hint}</p>}
      {error === null ? null : <p id={errorId} role="alert" className="text-sm text-destructive">{error}</p>}
    </div>
  )
}
