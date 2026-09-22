import { Link } from '@tanstack/react-router'
import { ShieldXIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { IAM_COPY } from '@/shared/copy/iam'

export type AccessDeniedStateProps = {
  readonly onRetry: () => void
}

export function AccessDeniedState({ onRetry }: AccessDeniedStateProps) {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div role="alert" className="flex max-w-md flex-col items-center gap-3 text-center">
        <ShieldXIcon aria-hidden className="size-8 text-destructive" />
        <h1 className="text-xl font-semibold text-foreground">{IAM_COPY.accessDenied.title}</h1>
        <p className="text-sm text-muted-foreground">{IAM_COPY.accessDenied.description}</p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button type="button" variant="outline" onClick={onRetry}>
            {IAM_COPY.accessDenied.retry}
          </Button>
          <Button render={<Link to="/" />}>{IAM_COPY.accessDenied.backToChat}</Button>
        </div>
      </div>
    </main>
  )
}