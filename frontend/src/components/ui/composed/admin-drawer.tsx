import type { ReactNode } from 'react'

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

export type AdminDrawerProps = {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly title: string
  readonly description?: string
  readonly children: ReactNode
  readonly footer?: ReactNode
  readonly className?: string
}

// SPEC-020 §3.2 / FIX-13. The legacy drawer was a positioned `<div>`: Tab walked straight out of
// it into the page behind, Escape did nothing, and closing it dropped focus onto `<body>`. This is
// a Base UI dialog, which owns the focus trap, the Escape and backdrop dismissals and the restore
// to the element that opened it — so every admin drawer inherits all four by construction rather
// than by each one remembering.
export function AdminDrawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: AdminDrawerProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent aria-label={title} className={cn('overflow-y-auto', className)}>
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          {description !== undefined ? <SheetDescription>{description}</SheetDescription> : null}
        </SheetHeader>
        <div className="flex-1 px-4 pb-4">{children}</div>
        {footer}
      </SheetContent>
    </Sheet>
  )
}
