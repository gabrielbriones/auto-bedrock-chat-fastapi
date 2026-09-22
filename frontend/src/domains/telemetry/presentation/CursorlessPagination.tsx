import { Button } from '@/components/ui/button'
import { TELEMETRY_COPY } from '@/shared/copy/telemetry'
import type { CursorlessPage } from '@/domains/telemetry/domain/public'

export type CursorlessPaginationProps = {
  readonly page: CursorlessPage
  readonly onNavigate: (offset: number) => void
}

export function CursorlessPagination({ page, onNavigate }: CursorlessPaginationProps) {
  if (page.rowCount === 0 && page.offset === 0) return null

  return (
    <nav aria-label={TELEMETRY_COPY.pagination.label} className="flex flex-wrap items-center justify-between gap-3 pt-3">
      <p className="text-sm text-muted-foreground">
        {page.rowCount === 0
          ? TELEMETRY_COPY.pagination.empty
          : TELEMETRY_COPY.pagination.range(page.offset + 1, page.offset + page.rowCount)}
      </p>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={!page.hasPrev}
          onClick={() => onNavigate(Math.max(0, page.offset - page.limit))}
        >
          {TELEMETRY_COPY.pagination.previous}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!page.hasNext}
          onClick={() => onNavigate(page.offset + page.limit)}
        >
          {TELEMETRY_COPY.pagination.next}
        </Button>
      </div>
    </nav>
  )
}