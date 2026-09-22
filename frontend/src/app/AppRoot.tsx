import { useEffect, useState } from 'react'
import { RouterProvider } from '@tanstack/react-router'

import { BootstrapProvider } from '@/app/bootstrap/BootstrapProvider'
import { useContainer } from '@/app/bootstrap/container-context'
import { ConfirmationHost } from '@/app/providers/ConfirmationHost'
import { ChatSocketProvider } from '@/app/providers/ChatSocketProvider'
import { ThemeProvider } from '@/app/providers/ThemeProvider'
import { useTheme } from '@/app/providers/theme-context'
import { createAppRouter } from '@/app/router'
import { OfflineBanner } from '@/components/ui/composed/offline-banner'
import { Toaster } from '@/components/ui/sonner'
import { AuthDialogHost } from '@/domains/iam/presentation/AuthDialogHost'

// The router is created after bootstrap resolves, because its context carries the Container
// (FR-SHELL-028) — which does not exist until BC-001's payload has been mapped.
function RouterHost() {
  const container = useContainer()
  const [router] = useState(() => createAppRouter(container))
  const { logger } = container
  // The vendored Toaster otherwise asks next-themes, which is not this app's theme mechanism.
  const { resolved } = useTheme()

  // FR-SHELL-023: a rejection no boundary can see still reaches the sink, once, without content.
  useEffect(() => {
    const onRejection = (event: PromiseRejectionEvent) => {
      logger.error('unhandled_rejection', { reason: String(event.reason) })
    }

    window.addEventListener('unhandledrejection', onRejection)

    return () => {
      window.removeEventListener('unhandledrejection', onRejection)
    }
  }, [logger])

  return (
    <>
      <OfflineBanner connectivity={container.connectivity} />
      <RouterProvider router={router} />
      {/* SPEC-021 §5: one toast outlet and one confirmation host for the whole application. */}
      <Toaster position="bottom-right" closeButton theme={resolved} />
      <ConfirmationHost controller={container.confirmations} />
      <AuthDialogHost />
    </>
  )
}

export function AppRoot() {
  return (
    <ThemeProvider>
      <BootstrapProvider>
        <ChatSocketProvider>
          <RouterHost />
        </ChatSocketProvider>
      </BootstrapProvider>
    </ThemeProvider>
  )
}
