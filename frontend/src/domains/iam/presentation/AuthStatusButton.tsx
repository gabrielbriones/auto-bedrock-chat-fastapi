import { LogInIcon } from 'lucide-react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Button } from '@/components/ui/button'
import { IAM_COPY } from '@/shared/copy/iam'

// FR-IAM-016: the sign-in entry point. Absent once authenticated — that identity now lives in the
// `UserMenu` at the foot of the conversation roster — and absent entirely when the deployment has
// authentication switched off, since there is no identity to show or discard.
export function AuthStatusButton() {
  const { identity, authPolicy } = useContainer()
  const session = useContainerStore('identity')

  if (!authPolicy.enabled || session.status === 'authenticated') {
    return null
  }

  return (
    <Button type="button" variant="ghost" size="sm" onClick={() => identity.openDialog()}>
      <LogInIcon aria-hidden />
      {IAM_COPY.status.logIn}
    </Button>
  )
}

