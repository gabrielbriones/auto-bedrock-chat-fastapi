import type { ReactNode } from 'react'
import { CatchBoundary, type ErrorComponentProps } from '@tanstack/react-router'

import { ErrorState } from '@/components/ui/composed/error-state'
import { SHELL } from '@/shared/copy/shell'

export type BoundaryProps = {
  // Resetting on this key is what makes navigating away from a broken view recover it.
  readonly resetKey: string
  readonly onCatch?: (error: Error) => void
  readonly children: ReactNode
}

const boundary = (description: string) =>
  function Boundary({ resetKey, onCatch, children }: BoundaryProps) {
    return (
      <CatchBoundary
        getResetKey={() => resetKey}
        {...(onCatch !== undefined ? { onCatch } : {})}
        errorComponent={({ reset }: ErrorComponentProps) => (
          <ErrorState description={description} onRetry={reset} />
        )}
      >
        {children}
      </CatchBoundary>
    )
  }

// FR-SHELL-009: the route and overlay levels of the three-level boundary set — the root level is
// the root route's own `errorComponent`. Both are built on the router's CatchBoundary rather than
// a hand-written class component, which STD-001 §6 bans.
export const RouteBoundary = boundary(SHELL.error.routeDescription)

// A crashing drawer must never take down the view behind it.
export const OverlayBoundary = boundary(SHELL.error.overlayDescription)
