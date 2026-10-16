import { useEffect } from 'react'

import { useContainer } from '@/app/bootstrap/container-context'

// FR-CONV-001. The composition root owns this dependency so `conv` never imports `iam`: the
// conversation store is told whether a principal is authenticated, not how that was decided.
export const useConversationVisibility = (): void => {
  const { conversations, identity } = useContainer()

  // Subscribed directly rather than through a render: a reconnect passes through
  // authenticated → authenticating → authenticated, and a batched render could see only the ends,
  // skipping the verification that reloads the conversation (XMGPLAT-11999).
  useEffect(() => {
    const sync = () => {
      const { status, principal } = identity.getSnapshot()
      const verified = status === 'authenticated'
      // A principal being re-verified after a reconnect keeps its conversations on screen; hiding
      // them wiped the transcript to the welcome screen.
      const signedIn = verified || (status === 'authenticating' && principal.mode !== 'anonymous')
      conversations.setAuthenticated(signedIn, verified)
    }

    sync()
    return identity.subscribe(sync)
  }, [conversations, identity])
}
