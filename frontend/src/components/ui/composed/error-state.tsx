import type { ReactNode } from 'react'
import { AlertTriangleIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { SHELL } from '@/shared/copy/shell'

// The build identity every error report carries (STD-001 §9). CI injects it; a local build shows
// `dev`, which is still a usable answer to "which build was this?".
export const buildSha = (): string => {
  const injected = import.meta.env.VITE_BUILD_SHA as string | undefined

  return injected !== undefined && injected.length > 0 ? injected : 'dev'
}

export type ErrorStateProps = {
  readonly title?: string
  readonly description: string
  readonly reference?: string
  readonly onRetry?: () => void
  readonly action?: ReactNode
}

// SPEC-020 §3.2 / FR-SHELL-009: one recoverable error presentation, used by every boundary. It
// always offers a way forward and always names the build, so a screenshot is a usable bug report.
export function ErrorState({ title, description, reference, onRetry, action }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-3 p-8 text-center"
    >
      <AlertTriangleIcon aria-hidden className="size-6 text-destructive" />
      <h2 className="text-lg font-medium text-foreground">{title ?? SHELL.error.title}</h2>
      <p className="max-w-prose text-sm text-muted-foreground">{description}</p>
      <p className="text-xs text-muted-foreground">
        {SHELL.error.reference(reference === undefined ? buildSha() : `${reference} · ${buildSha()}`)}
      </p>
      {onRetry !== undefined ? (
        <Button type="button" onClick={onRetry}>
          {SHELL.error.retry}
        </Button>
      ) : null}
      {action}
    </div>
  )
}
