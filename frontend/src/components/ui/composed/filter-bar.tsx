import { useEffect, useId, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ADMIN_COPY } from '@/shared/copy/admin'

/** FR-REV-002 / SPEC-020 §3.2: selects apply immediately, free text and dates settle first. */
export const FILTER_DEBOUNCE_MS = 200

export type FilterBarProps = {
  readonly label?: string
  readonly children: ReactNode
  readonly onReset?: () => void
  readonly resetLabel?: string
}

// SPEC-020 §3.2: no Apply button. The legacy `.filter-bar` had none either, and adding one would
// be a parity regression — the point of this primitive is that the URL is the applied state.
// `role="search"` rather than a `<search>` element: React 19 still renders that tag without its
// implicit role, so the landmark would be invisible to assistive tech.
export function FilterBar({ label, children, onReset, resetLabel }: FilterBarProps) {
  return (
    <div role="search" aria-label={label ?? ADMIN_COPY.filters.label}>
      <div className="flex flex-wrap items-end gap-3 py-2">
        {children}
        {onReset !== undefined ? (
          <Button type="button" variant="ghost" size="sm" onClick={onReset}>
            {resetLabel ?? ADMIN_COPY.filters.reset}
          </Button>
        ) : null}
      </div>
    </div>
  )
}

export type FilterFieldProps = {
  readonly label: string
  readonly children: (id: string) => ReactNode
}

export function FilterField({ label, children }: FilterFieldProps) {
  const id = useId()

  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </Label>
      {children(id)}
    </div>
  )
}

export type DebouncedFilterInputProps = {
  readonly id: string
  readonly value: string
  readonly onCommit: (value: string) => void
  readonly type?: 'text' | 'search' | 'date'
  readonly placeholder?: string
  readonly delayMs?: number
}

// The typed value is local until it settles, so every keystroke does not become a request. The
// commit is a prop call rather than a state update, which is what keeps this out of the
// `set-state-in-effect` trap; the external `value` is the authority whenever the two agree.
export function DebouncedFilterInput({
  id,
  value,
  onCommit,
  type = 'text',
  placeholder,
  delayMs = FILTER_DEBOUNCE_MS,
}: DebouncedFilterInputProps) {
  const [draft, setDraft] = useState(value)

  useEffect(() => {
    if (draft === value) {
      return
    }

    const timer = setTimeout(() => {
      onCommit(draft)
    }, delayMs)

    return () => {
      clearTimeout(timer)
    }
  }, [draft, value, onCommit, delayMs])

  return (
    <Input
      id={id}
      type={type}
      value={draft}
      {...(placeholder !== undefined ? { placeholder } : {})}
      onChange={(event) => {
        setDraft(event.target.value)
      }}
    />
  )
}
