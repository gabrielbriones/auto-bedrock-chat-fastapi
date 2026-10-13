import { useState } from 'react'
import { TriangleAlertIcon, XIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { IAM_COPY } from '@/shared/copy/iam'

export function DevModeBanner() {
  const [dismissed, setDismissed] = useState(false)

  if (dismissed) {
    return null
  }

  return (
    <div role="alert" className="flex items-start gap-3 border-b border-warning/40 bg-warning/10 px-4 py-3 text-warning-foreground">
      <TriangleAlertIcon aria-hidden className="mt-0.5 size-5 shrink-0" />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-semibold">{IAM_COPY.devMode.title}</p>
        <p>{IAM_COPY.devMode.description}</p>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={IAM_COPY.devMode.dismiss}
        title={IAM_COPY.devMode.dismiss}
        onClick={() => setDismissed(true)}
      >
        <XIcon aria-hidden />
      </Button>
    </div>
  )
}