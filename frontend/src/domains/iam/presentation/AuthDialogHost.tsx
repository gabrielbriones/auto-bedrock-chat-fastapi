import { useEffect } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { currentPath, locationParam } from '@/app/browser-location'
import { AuthDialog } from '@/domains/iam/presentation/AuthDialog'

// SPEC-021 §5 pattern: one host for the whole application, mounted beside the confirmation host.
// Applies the `auth_method` deep link once at startup (FR-IAM-007). It does NOT open the socket —
// ChatSocketProvider (ADR-004) is the sole owner of connect()/dispose(); a second owner here
// raced it under React 18 StrictMode's dev-only effect replay, producing extra short-lived
// connections (and, with them, a window where a conversation_list reply could land on a socket
// the app had already abandoned, leaving the sidebar roster stuck on just the newest thread).
export function AuthDialogHost() {
  const { identity, authPolicy } = useContainer()
  const session = useContainerStore('identity')

  useEffect(() => {
    // FR-IAM-007: read once from the address bar, not from router state — this is a startup
    // concern, and only `auth_method` is ever consulted.
    identity.applyDeepLink(locationParam('auth_method'), currentPath())
  }, [identity])

  return (
    <AuthDialog
      // Kept mounted and driven by `open`: Base UI applies the modal's inertness and restores
      // focus on close, neither of which can happen if the dialog unmounts instead (FR-IAM-013).
      key={session.preselectedKind ?? 'default'}
      policy={authPolicy}
      open={session.dialogOpen}
      preselectedKind={session.preselectedKind}
      submitting={session.status === 'authenticating'}
      onSubmit={(credential) => {
        identity.submit(credential)
      }}
      onSkip={() => {
        identity.skip()
      }}
      // FR-IAM-005: the current location is preserved across the IdP round trip.
      onSsoLogin={() => {
        identity.beginSsoLogin(currentPath())
      }}
    />
  )
}
