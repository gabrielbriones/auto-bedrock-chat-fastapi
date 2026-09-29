import { Button } from '@/components/ui/button'
import { ADMIN_COPY } from '@/shared/copy/admin'
import type { OffsetWindow } from '@/shared/kernel/pagination'

export type OffsetPaginationProps = {
  readonly range: OffsetWindow
  readonly onNavigate: (offset: number) => void
  readonly label?: string
}

// SPEC-020 §3.2 / FR-REV-009. FIX-06: the legacy `renderPagination` returned early whenever
// `total` was 0, so a reviewer who had paged deep into a list that then shrank — after a bulk
// delete, or after tightening a filter — was left on an empty page with no control at all. Here
// the controls disappear only when there is genuinely nowhere to go: the first page of an empty
// list. Any other empty page still renders, with Previous enabled.
export function OffsetPagination({ range, onNavigate, label }: OffsetPaginationProps) {
  if (range.total === 0 && !range.hasPrevious) {
    return null
  }

  const isEmptyPage = range.from === 0

  return (
    <nav
      aria-label={label ?? ADMIN_COPY.pagination.label}
      className="flex items-center justify-between gap-4 py-2"
    >
      <p className="text-sm text-muted-foreground" aria-live="polite">
        {isEmptyPage
          ? ADMIN_COPY.pagination.emptyPage
          : ADMIN_COPY.pagination.range(range.from, range.to, range.total)}
      </p>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!range.hasPrevious}
          onClick={() => {
            onNavigate(range.previousOffset)
          }}
        >
          {ADMIN_COPY.pagination.previous}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!range.hasNext}
          onClick={() => {
            onNavigate(range.nextOffset)
          }}
        >
          {ADMIN_COPY.pagination.next}
        </Button>
      </div>
    </nav>
  )
}
