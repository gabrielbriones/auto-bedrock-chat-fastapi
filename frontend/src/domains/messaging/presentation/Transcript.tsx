import type { ReactNode } from 'react'
import type { MessageId } from '@/shared/kernel/branded'

import { MESSAGING_COPY } from '@/shared/copy/messaging'

import type { TranscriptEntry } from '@/domains/messaging/domain/transcript'
import { JumpToLatestButton } from '@/domains/messaging/presentation/JumpToLatestButton'
import { MessageBubble } from '@/domains/messaging/presentation/MessageBubble'
import { VirtualEntries } from '@/domains/messaging/presentation/VirtualEntries'
import { WelcomeState } from '@/domains/messaging/presentation/WelcomeState'
import { useStickToBottom } from '@/domains/messaging/presentation/useStickToBottom'
import { useThrottledValue } from '@/domains/messaging/presentation/useThrottledValue'

const { transcript: COPY } = MESSAGING_COPY

/** Below this a plain list is cheaper than measuring rows; above it the DOM cost stops being flat. */
const VIRTUALISE_ABOVE = 100
const ANNOUNCE_INTERVAL_MS = 1000

// What assistive technology is told the transcript is doing. Deliberately a *status* while a turn
// is in flight — snapshots change far too fast to be worth reading aloud — and the finished answer
// announced once, in full, when it settles.
const announcementFor = (entries: readonly TranscriptEntry[]): string => {
  const latest = entries.at(-1)

  if (latest === undefined || latest.message.role === 'user') {
    return ''
  }

  if (latest.progress) {
    return latest.message.raw
  }

  return latest.streaming || latest.pending ? COPY.responding : latest.message.raw
}

export type TranscriptProps = {
  readonly entries: readonly TranscriptEntry[]
  readonly welcome: string
  readonly presetArea?: ReactNode
  readonly renderFeedback?: (messageId: MessageId) => ReactNode
}

// FR-MSG-028. The container itself is not a live region: it would announce the whole transcript on
// mount and every snapshot after it — and, once virtualised, announce scrolling as if it were new
// content. The single throttled region below is the only thing that speaks.
export function Transcript({ entries, welcome, presetArea, renderFeedback }: TranscriptProps) {
  const { stuck, unread, jumpToLatest } = useStickToBottom(entries, entries.length)
  const announcement = useThrottledValue(announcementFor(entries), ANNOUNCE_INTERVAL_MS)

  return (
    <section aria-label={COPY.label} className="flex flex-1 flex-col gap-4 p-4">
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {entries.length === 0 ? (
        <WelcomeState message={welcome}>{presetArea}</WelcomeState>
      ) : null}

      {entries.length > VIRTUALISE_ABOVE ? (
        <VirtualEntries
          entries={entries}
          {...(renderFeedback === undefined ? {} : { renderFeedback })}
        />
      ) : (
        entries.map((entry) => (
          <MessageBubble
            entry={entry}
            key={entry.key}
            {...(renderFeedback === undefined ? {} : { renderFeedback })}
          />
        ))
      )}

      {stuck ? null : <JumpToLatestButton unread={unread} onJump={jumpToLatest} />}
    </section>
  )
}
