import { Skeleton } from '@/components/ui/skeleton'
import { SHELL } from '@/shared/copy/shell'

export function RoutePending() {
  return (
    <div role="status" aria-live="polite" className="flex min-h-[50vh] items-center justify-center px-6">
      <div className="grid w-full max-w-xs gap-3">
        <p className="text-sm font-medium text-muted-foreground">{SHELL.loading}</p>
        <Skeleton aria-hidden="true" className="h-1.5 w-full rounded-full bg-primary/40" />
      </div>
    </div>
  )
}