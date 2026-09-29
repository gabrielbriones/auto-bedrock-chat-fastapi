import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export type PageHeaderProps = {
  readonly title: string
  readonly description?: string
  readonly actions?: ReactNode
  readonly className?: string
}

// SPEC-020 §3.2: the one heading block every admin view opens with, so titles, descriptions and
// header actions line up from view to view instead of each page choosing its own h1 styling.
export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('flex flex-wrap items-end justify-between gap-4', className)}>
      <div className="grid gap-1">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description !== undefined ? (
          <p className="max-w-prose text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions !== undefined ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  )
}

export type PageProps = {
  readonly children: ReactNode
  readonly className?: string
}

// The admin content column: one width, one gutter, one vertical rhythm for every view.
export function Page({ children, className }: PageProps) {
  return (
    <section className={cn('mx-auto grid w-full max-w-7xl gap-6 p-4 sm:p-6 lg:p-8', className)}>
      {children}
    </section>
  )
}
