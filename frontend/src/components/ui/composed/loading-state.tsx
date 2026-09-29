import { Skeleton } from '@/components/ui/skeleton'
import { SHELL } from '@/shared/copy/shell'
import { cn } from '@/lib/utils'

export type LoadingStateProps = {
  readonly label?: string
  readonly rows?: number
  readonly className?: string
}

// SPEC-020 §3.2: skeletons rather than spinners, and always announced — a lazy admin chunk must
// never present as a blank page (FR-SHELL-015).
export function LoadingState({ label, rows = 3, className }: LoadingStateProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={label ?? SHELL.loading}
      className={cn('flex flex-col gap-3 p-6', className)}
    >
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-6 w-full" />
      ))}
    </div>
  )
}
