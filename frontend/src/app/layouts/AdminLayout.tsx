import { useCallback, useEffect, type ReactNode } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import {
  ArrowLeftIcon,
  BarChart3Icon,
  BookOpenIcon,
  CheckCircle2Icon,
  CoinsIcon,
  DatabaseIcon,
  InboxIcon,
  type LucideIcon,
} from 'lucide-react'

import { useContainer } from '@/app/bootstrap/container-context'
import { AppShell } from '@/app/layouts/AppShell'
import { RouteBoundary } from '@/components/ui/composed/error-boundaries'
import { buttonVariants } from '@/components/ui/button'
import { SHELL } from '@/shared/copy/shell'
import { useDocumentTitle } from '@/app/layouts/use-document-title'
import type { Capabilities } from '@/domains/iam/domain/public'
import { DevModeBanner } from '@/domains/iam/presentation/dev-mode-banner'
import { PendingBadge } from '@/domains/review/presentation/PendingBadge'

type NavItem = {
  readonly to: '/dashboard/feedback' | '/dashboard/reviewed' | '/dashboard/feedback/stats' | '/dashboard/kb-browser' | '/dashboard/kb-sources' | '/dashboard/token-usages'
  readonly label: string
  readonly icon: LucideIcon
  /** Sections that exist only when the deployment enables them are hidden, as the route itself rejects them. */
  readonly enabled?: (capabilities: Capabilities) => boolean
}

const NAV_ITEMS: readonly NavItem[] = [
  { to: '/dashboard/feedback', label: SHELL.admin.feedbackQueue, icon: InboxIcon },
  { to: '/dashboard/reviewed', label: SHELL.admin.reviewed, icon: CheckCircle2Icon },
  { to: '/dashboard/feedback/stats', label: SHELL.admin.stats, icon: BarChart3Icon },
  { to: '/dashboard/kb-browser', label: SHELL.admin.knowledge, icon: BookOpenIcon },
  { to: '/dashboard/kb-sources', label: SHELL.admin.kbSources, icon: DatabaseIcon, enabled: (capabilities) => capabilities.kbSourceIngestionEnabled },
  { to: '/dashboard/token-usages', label: SHELL.admin.usage, icon: CoinsIcon, enabled: (capabilities) => capabilities.tokenUsageEnabled },
]

export type AdminLayoutProps = {
  readonly children: ReactNode
  readonly capabilities: Capabilities
}

// The active link carries a short bright-blue bar on its leading edge over the accent fill.
function AdminNav({ capabilities }: { readonly capabilities: Capabilities }) {
  return (
    <nav aria-label={SHELL.admin.navigation} className="flex h-full flex-col gap-1 p-3">
      <p className="px-3 pt-2 pb-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
        {SHELL.admin.navigation}
      </p>
      {NAV_ITEMS.filter((item) => item.enabled === undefined || item.enabled(capabilities)).map((item) => (
        <Link
          key={item.to}
          to={item.to}
          preload="render"
          // Path only: a filtered or paged list is still the same section (its filters live in the URL).
          activeOptions={{ exact: true, includeSearch: false }}
          className="group/nav relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring aria-[current=page]:bg-accent aria-[current=page]:font-medium aria-[current=page]:text-accent-foreground before:absolute before:top-2 before:bottom-2 before:left-0 before:w-0.5 before:rounded-full before:bg-energy before:opacity-0 before:transition-opacity aria-[current=page]:before:opacity-100"
        >
          <item.icon aria-hidden className="size-4 shrink-0 opacity-80 group-aria-[current=page]/nav:opacity-100" />
          <span className="flex-1 truncate">{item.label}</span>
          {item.to === '/dashboard/feedback' ? <PendingBadge /> : null}
        </Link>
      ))}
    </nav>
  )
}

// SPEC-021 §3.2. The rail is persistent at `lg` and above; collapsing it into a sheet below that
// (FR-DS-007) needs the trigger the admin header brings with `T-030`. The pending-review badge
// belongs to the review context.
export function AdminLayout({ children, capabilities }: AdminLayoutProps) {
  const { bootstrap, logger, reviews } = useContainer()
  const { pathname, view } = useRouterState({
    select: (state) => ({
      pathname: state.location.pathname,
      view: state.matches.at(-1)?.staticData.title,
    }),
  })

  useDocumentTitle(view === undefined ? bootstrap.appTitle : `${view} — ${bootstrap.appTitle}`)

  // FR-REV-020: probed once when the admin shell mounts; every mutation that can change it
  // refreshes it again from `ReviewStore` itself (FR-REV-021).
  useEffect(() => {
    void reviews.refreshPendingCount()
  }, [reviews])

  const report = useCallback(
    (error: Error) => {
      logger.error('route_render_failed', { route: pathname, name: error.name })
    },
    [logger, pathname],
  )

  return (
    <AppShell
      mainLabel={SHELL.admin.landmark}
      header={
        <header className="surface-glow flex h-14 items-center justify-between gap-4 border-b border-border px-4">
          <div className="flex min-w-0 items-center gap-3">
            <p className="truncate font-semibold text-foreground">{bootstrap.uiTitle}</p>
            <span className="hidden rounded-full border border-border bg-background/60 px-2 py-0.5 text-xs font-medium tracking-wide text-muted-foreground uppercase sm:inline">
              {SHELL.admin.landmark}
            </span>
          </div>
          <Link to="/ui" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
            <ArrowLeftIcon aria-hidden />
            {SHELL.admin.backToChat}
          </Link>
        </header>
      }
      sidebar={<AdminNav capabilities={capabilities} />}
    >
      {capabilities.isAnonymousAdmin ? <DevModeBanner /> : null}
      <RouteBoundary resetKey={pathname} onCatch={report}>
        {children}
      </RouteBoundary>
    </AppShell>
  )
}

