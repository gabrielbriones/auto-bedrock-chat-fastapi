import { memo, type ReactNode } from 'react'

import { cn } from '@/lib/utils'
import type { MessageId } from '@/shared/kernel/branded'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

import type { MessageRole } from '@/domains/messaging/domain/message'
import type { TranscriptEntry } from '@/domains/messaging/domain/transcript'
import { KbCitations } from '@/domains/messaging/presentation/KbCitations'
import { MarkdownView } from '@/domains/messaging/presentation/MarkdownView'
import { stripReasoningBlocks } from '@/domains/messaging/presentation/redaction'
import { ToolActivityDisclosure } from '@/domains/messaging/presentation/ToolActivityDisclosure'

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
    left.renderFeedback === right.renderFeedback
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

  if (entry.progress) {
    return (
      <div
        className={cn('max-w-prose rounded-lg px-4 py-2 text-sm italic', BUBBLE_CLASS.assistant, 'opacity-80')}
      >
        {message.raw}
      </div>
    )
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
        'max-w-prose rounded-lg px-4 py-2',
        // Markdown already renders its own block spacing; pre-wrap would double every line break.
        plainText && 'whitespace-pre-wrap',
        BUBBLE_CLASS[message.role],
        entry.transient && 'border border-dashed border-border bg-transparent',
      )}
    >
      {plainText ? message.raw : <MarkdownView content={body} />}
      {entry.interrupted ? (
        <p className="text-sm text-muted-foreground" role="status">
          {COPY.interrupted}
        </p>
      ) : null}
      {entry.activity?.truncated ? (
        <p className="mt-3 border-t border-border pt-3 text-sm text-muted-foreground" role="status">
          {COPY.truncated}
        </p>
      ) : null}
      {entry.activity !== null ? <ToolActivityDisclosure activity={entry.activity} /> : null}
      {entry.activity !== null ? <KbCitations citations={entry.activity.citations} /> : null}
      {canRenderFeedback ? renderFeedback(message.id) : null}
    </article>
  )
}, sameEntry)