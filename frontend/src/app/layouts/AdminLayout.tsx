import { useCallback, useEffect, type ReactNode } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { ArrowLeftIcon } from 'lucide-react'

import { useContainer } from '@/app/bootstrap/container-context'
import { AppShell } from '@/app/layouts/AppShell'
import { RouteBoundary } from '@/components/ui/composed/error-boundaries'
import { buttonVariants } from '@/components/ui/button'
import { SHELL } from '@/shared/copy/shell'
import { useDocumentTitle } from '@/app/layouts/use-document-title'
import type { Capabilities } from '@/domains/iam/domain/public'
import { DevModeBanner } from '@/domains/iam/presentation/dev-mode-banner'
import { PendingBadge } from '@/domains/review/presentation/PendingBadge'

const NAV_ITEMS = [
  { to: '/admin/feedback', label: SHELL.admin.feedbackQueue },
  { to: '/admin/feedback/reviewed', label: SHELL.admin.reviewed },
  { to: '/admin/feedback/stats', label: SHELL.admin.stats },
  { to: '/admin/knowledge', label: SHELL.admin.knowledge },
  { to: '/admin/usage', label: SHELL.admin.usage },
] as const

export type AdminLayoutProps = {
  readonly children: ReactNode
  readonly capabilities: Capabilities
}

function AdminNav({ capabilities }: { readonly capabilities: Capabilities }) {
  return (
    <nav aria-label={SHELL.admin.navigation} className="flex flex-col gap-1 p-4">
      {NAV_ITEMS.filter((item) => item.to !== '/admin/usage' || capabilities.tokenUsageEnabled).map((item) => (
        <Link
          key={item.to}
          to={item.to}
          activeOptions={{ exact: true }}
          className="flex items-center justify-between rounded-md px-3 py-2 text-sm text-foreground hover:bg-accent aria-[current=page]:bg-accent aria-[current=page]:font-medium"
        >
          <span>{item.label}</span>
          {item.to === '/admin/feedback' ? <PendingBadge /> : null}
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
        <header className="flex h-14 items-center justify-between border-b border-border px-4">
          <p className="truncate font-medium text-foreground">{bootstrap.uiTitle}</p>
          <Link to="/" className={buttonVariants({ variant: 'ghost' })}>
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

