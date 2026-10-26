import { memo, type ReactNode } from 'react'

import { cn } from '@/lib/utils'
import type { MessageId } from '@/shared/kernel/branded'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

import type { MessageRole } from '@/domains/messaging/domain/message'
import type { InterruptionReason, TranscriptEntry } from '@/domains/messaging/domain/transcript'
import { KbCitations } from '@/domains/messaging/presentation/KbCitations'
import { MarkdownView } from '@/domains/messaging/presentation/MarkdownView'
import { stripReasoningBlocks } from '@/domains/messaging/presentation/redaction'
import { ToolCallList } from '@/domains/messaging/presentation/ToolCallList'
import { rowsFromActivity, rowsFromLive } from '@/domains/messaging/presentation/tool-call-rows'

const { transcript: COPY } = MESSAGING_COPY

const ROLE_LABEL: Record<MessageRole, string> = {
  user: COPY.user,
  assistant: COPY.assistant,
  system: COPY.system,
}

const BUBBLE_CLASS: Record<MessageRole, string> = {
  user: 'ms-auto bg-message-user-bg text-message-user-fg',
  assistant: 'bg-message-assistant-bg text-message-assistant-fg',
  system: 'bg-muted text-muted-foreground',
}

// A dropped socket is a soft notice; a turn the server cut off mid execution is an error.
const INTERRUPTION_NOTICE: Record<InterruptionReason, { readonly text: string; readonly className: string }> = {
  'connection-lost': { text: COPY.interrupted, className: 'text-muted-foreground' },
  execution: { text: COPY.interruptedExecution, className: 'text-destructive' },
}

export type MessageBubbleProps = {
  readonly entry: TranscriptEntry
  readonly renderFeedback?: (messageId: MessageId) => ReactNode
}

const sameEntry = (left: MessageBubbleProps, right: MessageBubbleProps): boolean => {
  const previous = left.entry
  const next = right.entry
  return (
    previous.message === next.message &&
    previous.streaming === next.streaming &&
    previous.transient === next.transient &&
    previous.progress === next.progress &&
    previous.interrupted === next.interrupted &&
    previous.activity === next.activity &&
    previous.liveTools === next.liveTools &&
    left.renderFeedback === right.renderFeedback
  )
}

// The in-flight turn: every tool call so far, live, then whatever the model is doing now.
function InFlightBubble({ entry }: { readonly entry: TranscriptEntry }) {
  const liveTools = entry.liveTools ?? []

  if (liveTools.length === 0) {
    return (
      <div
        className={cn('max-w-[85%] rounded-lg px-4 py-2 text-sm italic', BUBBLE_CLASS.assistant)}
      >
        <span className="text-shimmer">{entry.message.raw}</span>
      </div>
    )
  }

  const toolRunning = liveTools.some((tool) => tool.status === 'running')
  const status = entry.progress ? entry.message.raw : toolRunning ? null : COPY.thinking

  return (
    <div className={cn('max-w-[85%] space-y-2 rounded-lg px-4 py-2', BUBBLE_CLASS.assistant)}>
      <ToolCallList live rows={rowsFromLive(liveTools)} className="-mx-2" />
      {entry.streaming && !entry.progress ? (
        <p className="whitespace-pre-wrap">{entry.message.raw}</p>
      ) : status !== null ? (
        <p className="text-sm italic">
          <span className="text-shimmer">{status}</span>
        </p>
      ) : null}
    </div>
  )
}

export const MessageBubble = memo(function MessageBubble({ entry, renderFeedback }: MessageBubbleProps) {
  const { message } = entry
  // An interrupted turn's text stopped mid-token, so it stays plain for the same reason a stream does.
  // User turns are Markdown too (FIX-01): preset prompts carry headings and tables, and rendering
  // them verbatim is what made them a wall of text. Only system notices stay literal.
  const plainText =
    message.role === 'system' || (message.role === 'assistant' && (entry.streaming || entry.interrupted))
  const body = message.role === 'assistant' ? stripReasoningBlocks(message.raw) : message.raw

  if (entry.progress || (entry.liveTools !== undefined && entry.liveTools.length > 0)) {
    return <InFlightBubble entry={entry} />
  }

  const canRenderFeedback =
    renderFeedback !== undefined &&
    message.role === 'assistant' &&
    message.id !== null &&
    !entry.pending &&
    !entry.streaming &&
    !entry.transient &&
    !entry.interrupted

  return (
    <article
      aria-label={ROLE_LABEL[message.role]}
      className={cn(
        'max-w-[85%] rounded-lg px-4 py-2',
        // Markdown already renders its own block spacing; pre-wrap would double every line break.
        plainText && 'whitespace-pre-wrap',
        BUBBLE_CLASS[message.role],
        entry.transient && 'border border-dashed border-border bg-transparent',
      )}
    >
      {plainText ? message.raw : <MarkdownView content={body} />}
      {entry.interrupted !== null ? (
        <p className={cn('text-sm', INTERRUPTION_NOTICE[entry.interrupted].className)} role="status">
          {INTERRUPTION_NOTICE[entry.interrupted].text}
        </p>
      ) : null}
      {/* Once answered, the tool trail is supporting detail, so it follows the answer it produced. */}
      {entry.activity !== null ? (
        <ToolCallList
          rows={rowsFromActivity(entry.activity)}
          className={cn('-mx-2', body.trim() !== '' && 'mt-3 border-t border-border pt-2 dark:border-white/10')}
        />
      ) : null}
      {entry.activity?.truncated ? (
        <p className="mt-3 border-t border-border pt-3 text-sm text-muted-foreground" role="status">
          {COPY.truncated}
        </p>
      ) : null}
      {entry.activity !== null ? <KbCitations citations={entry.activity.citations} /> : null}
      {canRenderFeedback ? renderFeedback(message.id) : null}
    </article>
  )
}, sameEntry)