import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { LayoutDashboardIcon } from 'lucide-react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import {
  ConversationDrawer,
  ConversationDrawerTrigger,
} from '@/app/chat/ConversationDrawer'
import { useConversationVisibility } from '@/app/chat/useConversationVisibility'
import { AppShell } from '@/app/layouts/AppShell'
import { RouteBoundary } from '@/components/ui/composed/error-boundaries'
import { buttonVariants } from '@/components/ui/button'
import { SHELL } from '@/shared/copy/shell'
import { useDocumentTitle } from '@/app/layouts/use-document-title'
import { AuthStatusButton } from '@/domains/iam/presentation/AuthStatusButton'
import { UserMenu } from '@/domains/iam/presentation/UserMenu'
import { ConversationSidebar } from '@/domains/conversation/presentation/ConversationSidebar'
import { ConnectionBadge } from '@/domains/messaging/presentation/ConnectionBadge'
import { ModelConfigHeader } from '@/domains/model-config/presentation/ModelConfigHeader'

export type ChatLayoutProps = {
  readonly children: ReactNode
}

function AdminDashboardLink() {
  const { capabilityProbe } = useContainer()
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    let active = true

    void capabilityProbe.probe().then((capabilities) => {
      if (active) setVisible(capabilities.isAdmin)
    })

    return () => { active = false }
  }, [capabilityProbe])

  return visible ? (
    <Link to="/admin" className={buttonVariants({ variant: 'ghost' })}>
      <LayoutDashboardIcon aria-hidden />
      {SHELL.admin.openDashboard}
    </Link>
  ) : null
}

// SPEC-021 §3.1 / FR-SHELL-018. The roster is persistent at `lg` and up and lives in a drawer below
// it (T-025); both render the same `ConversationSidebar`, so there is one implementation of the
// list and its keyboard behaviour, not two.
export function ChatLayout({ children }: ChatLayoutProps) {
  const { bootstrap, logger } = useContainer()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const [drawerOpen, setDrawerOpen] = useState(false)

  useDocumentTitle(bootstrap.appTitle)
  useConversationVisibility()
  const { visible } = useContainerStore('conversations')
  const { connection, configuredModel } = useContainerStore('chatSession')

  const closeDrawer = useCallback(() => { setDrawerOpen(false) }, [])

  const openDrawer = useCallback(() => { setDrawerOpen(true) }, [])

  // FR-SHELL-023: reported once, with the route and nothing the user typed.
  const report = useCallback(
    (error: Error) => {
      logger.error('route_render_failed', { route: pathname, name: error.name })
    },
    [logger, pathname],
  )

  return (
    <AppShell
      mainLabel={SHELL.chat.transcript}
      header={
        <div>
          <header className="flex h-14 items-center gap-2 border-b border-border px-4">
            {visible ? <ConversationDrawerTrigger onOpen={openDrawer} /> : null}
            <h1 className="truncate font-medium text-foreground">{bootstrap.uiTitle}</h1>
            <div className="ms-auto flex items-center gap-2">
              {bootstrap.enableConfigSidebar ? <ModelConfigHeader configuredModel={configuredModel} /> : null}
              <AdminDashboardLink />
              <AuthStatusButton />
            </div>
          </header>
          <ConnectionBadge connection={connection} />
        </div>
      }
      sidebar={visible ? <ConversationSidebar footer={<UserMenu />} /> : undefined}
      overlays={
        <ConversationDrawer
          open={drawerOpen}
          onOpenChange={setDrawerOpen}
          onNavigate={closeDrawer}
          footer={<UserMenu />}
        />
      }
    >
      <RouteBoundary resetKey={pathname} onCatch={report}>
        {children}
      </RouteBoundary>
    </AppShell>
  )
}
