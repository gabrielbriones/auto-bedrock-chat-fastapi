import { useRef } from 'react'
import type { ReactNode } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'

import { shellScrollContainer } from '@/components/ui/composed/scroll-container'
import type { MessageId } from '@/shared/kernel/branded'

import type { TranscriptEntry } from '@/domains/messaging/domain/transcript'
import { MessageBubble } from '@/domains/messaging/presentation/MessageBubble'

// A first guess only: every row is measured once it mounts, which is what keeps the scrollbar and
// therefore the scroll position stable as rows of real height replace estimates.
const ESTIMATED_ROW_PX = 96
const OVERSCAN_ROWS = 8

export type VirtualEntriesProps = {
  readonly entries: readonly TranscriptEntry[]
  readonly renderFeedback?: (messageId: MessageId) => ReactNode
}

// NFR-PERF-005: a long transcript keeps a bounded number of bubbles in the DOM. It virtualises
// against the shell's <main> rather than a scroller of its own (FR-DS-008), so `scrollMargin`
// accounts for whatever the shell renders above the list.
export function VirtualEntries({ entries, renderFeedback }: VirtualEntriesProps) {
  const anchor = useRef<HTMLDivElement>(null)

  // TanStack Virtual owns its own memoisation, so the React Compiler skipping this component is
  // expected rather than a defect to fix.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualiser = useVirtualizer({
    count: entries.length,
    getScrollElement: shellScrollContainer,
    estimateSize: () => ESTIMATED_ROW_PX,
    getItemKey: (index) => entries[index]?.key ?? index,
    overscan: OVERSCAN_ROWS,
    scrollMargin: anchor.current?.offsetTop ?? 0,
  })

  return (
    <div ref={anchor} className="relative w-full" style={{ height: virtualiser.getTotalSize() }}>
      {virtualiser.getVirtualItems().map((item) => {
        const entry = entries[item.index]

        return entry === undefined ? null : (
          <div
            key={item.key}
            data-index={item.index}
            ref={virtualiser.measureElement}
            className="absolute inset-x-0 top-0 pb-4"
            style={{
              transform: `translateY(${item.start - virtualiser.options.scrollMargin}px)`,
            }}
          >
            <MessageBubble
              entry={entry}
              {...(renderFeedback === undefined ? {} : { renderFeedback })}
            />
          </div>
        )
      })}
    </div>
  )
}
