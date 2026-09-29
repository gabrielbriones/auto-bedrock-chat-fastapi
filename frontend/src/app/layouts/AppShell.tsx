import { useCallback, type ReactNode } from 'react'

import { MAIN_SCROLL_CONTAINER_ID } from '@/components/ui/composed/scroll-container'
import { SHELL } from '@/shared/copy/shell'
import { cn } from '@/lib/utils'

const MAIN_ID = MAIN_SCROLL_CONTAINER_ID

function SkipLink() {
  // jsdom and some browsers do not move focus on fragment navigation alone (NFR-A11Y-002).
  const focusMain = useCallback(() => {
    document.getElementById(MAIN_ID)?.focus()
  }, [])

  return (
    <a
      href={`#${MAIN_ID}`}
      onClick={focusMain}
      className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-foreground focus:shadow-md focus:outline-2 focus:outline-ring"
    >
      {SHELL.skipToContent}
    </a>
  )
}

export type AppShellProps = {
  readonly header?: ReactNode
  readonly sidebar?: ReactNode
  readonly footer?: ReactNode
  readonly overlays?: ReactNode
  readonly mainLabel?: string
  readonly children: ReactNode
}

// SPEC-021 §3: the frame both layouts compose. `h-dvh` + `overflow-hidden` at the root and a
// single `overflow-y-auto` on <main> is what guarantees exactly one scroll container
// (FR-SHELL-018, FR-DS-008/009) — no descendant may add another.
export function AppShell({
  header,
  sidebar,
  footer,
  overlays,
  mainLabel,
  children,
}: AppShellProps) {
  return (
    <div data-slot="app-shell" className="grid h-dvh grid-cols-1 grid-rows-[auto_1fr] overflow-hidden">
      <SkipLink />

      {header}

      <div
        className={cn(
          'grid min-h-0 min-w-0 grid-cols-1',
          sidebar !== undefined ? 'lg:grid-cols-[16rem_minmax(0,1fr)]' : undefined,
        )}
      >
        {sidebar !== undefined ? (
          <aside className="hidden min-h-0 border-r border-border lg:block">{sidebar}</aside>
        ) : null}

        <div className="grid min-h-0 min-w-0 grid-rows-[minmax(0,1fr)_auto]">
          <main
            id={MAIN_ID}
            tabIndex={0}
            aria-label={mainLabel ?? SHELL.mainLandmark}
            className="relative min-h-0 min-w-0 overflow-y-auto outline-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
          >
            {children}
          </main>

          {footer}
        </div>
      </div>

      {overlays}
    </div>
  )
}
