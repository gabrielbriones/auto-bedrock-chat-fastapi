import { useEffect } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'

// FR-CONV-001. The composition root owns this dependency so `conv` never imports `iam`: the
// conversation store is told whether a principal is authenticated, not how that was decided.
export const useConversationVisibility = (): void => {
  const { conversations } = useContainer()
  const { status, principal } = useContainerStore('identity')
  // A principal being re-verified after a reconnect keeps its conversations; hiding them
  // wiped the transcript to the welcome screen (XMGPLAT-11999).
  const signedIn =
    status === 'authenticated' || (status === 'authenticating' && principal.mode !== 'anonymous')

  useEffect(() => {
    conversations.setAuthenticated(signedIn)
  }, [conversations, signedIn])
}
