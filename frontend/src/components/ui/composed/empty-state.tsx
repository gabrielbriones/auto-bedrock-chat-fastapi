import type { ReactNode } from 'react'

export type EmptyStateProps = {
  readonly icon?: ReactNode
  readonly title: string
  readonly description?: string
  readonly action?: ReactNode
}

// SPEC-020 §3.2: the single "nothing here" presentation, so an empty table and an empty page do
// not drift apart.
export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
      {icon}
      <p className="text-base font-medium text-foreground">{title}</p>
      {description !== undefined ? (
        <p className="max-w-prose text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action}
    </div>
  )
}
