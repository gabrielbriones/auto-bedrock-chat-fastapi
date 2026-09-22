import { useEffect } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'

// FR-CONV-001. The composition root owns this dependency so `conv` never imports `iam`: the
// conversation store is told whether a principal is authenticated, not how that was decided.
export const useConversationVisibility = (): void => {
  const { conversations } = useContainer()
  const { status } = useContainerStore('identity')

  useEffect(() => {
    conversations.setAuthenticated(status === 'authenticated')
  }, [conversations, status])
}
