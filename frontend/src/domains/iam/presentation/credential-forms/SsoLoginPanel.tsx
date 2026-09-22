import { useState } from 'react'
import { Loader2Icon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { IAM_COPY } from '@/shared/copy/iam'

export type SsoLoginPanelProps = {
  readonly onLogin: () => void
  readonly disabled: boolean
}

// FR-IAM-005a: once activated the button is disabled and shows a spinner, because `beginLogin`
// navigates away and never returns.
export function SsoLoginPanel({ onLogin, disabled }: SsoLoginPanelProps) {
  const [redirecting, setRedirecting] = useState(false)

  return (
    <div className="grid gap-3">
      <p className="text-sm text-muted-foreground">{IAM_COPY.sso.description}</p>
      <Button
        type="button"
        disabled={disabled || redirecting}
        onClick={() => {
          setRedirecting(true)
          onLogin()
        }}
      >
        {redirecting ? (
          <>
            <Loader2Icon aria-hidden className="animate-spin" />
            {IAM_COPY.sso.redirecting}
          </>
        ) : (
          IAM_COPY.sso.login
        )}
      </Button>
    </div>
  )
}
