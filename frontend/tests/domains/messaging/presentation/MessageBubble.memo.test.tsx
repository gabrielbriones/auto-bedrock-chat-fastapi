import { render, screen } from '@testing-library/react'
import { describe, expect, it, jest } from '@jest/globals'

import { Instant } from '@/shared/kernel/instant'
import { messageId } from '@/shared/kernel/branded'
import { isOk } from '@/shared/kernel/result'
import type { TranscriptEntry } from '@/domains/messaging/domain/transcript'

// NFR-PERF-006. Deliberately *not* memoised, so it re-renders whenever its parent bubble does:
// the count below therefore measures `MessageBubble` renders rather than React's own bailouts.
const markdownRenders = jest.fn()

// ESM modules are linked before any test code runs, so the mock must be registered and the
// subject imported dynamically afterwards (there is no `jest.mock` hoisting under native ESM).
jest.unstable_mockModule('@/domains/messaging/presentation/MarkdownView', () => ({
  MarkdownView: ({ content }: { readonly content: string }) => {
    markdownRenders(content)
    return <span>{content}</span>
  },
}))

const { Transcript } = await import('@/domains/messaging/presentation/Transcript')

const atResult = Instant.fromEpochMilliseconds(0)
if (!isOk(atResult)) {
  throw new Error('Expected test instant to be valid')
}
const at = atResult.value

const answered: TranscriptEntry = {
  key: 'answered',
  message: { id: messageId('m-1'), role: 'assistant', raw: 'settled answer', at, seq: 1 },
  pending: false,
  streaming: false,
  transient: false,
  progress: false,
  interrupted: false,
  activity: null,
}

const streaming = (raw: string): TranscriptEntry => ({
  key: 'streaming',
  message: { id: null, role: 'assistant', raw, at, seq: 2 },
  pending: true,
  streaming: true,
  transient: false,
  progress: false,
  interrupted: false,
  activity: null,
})

describe('MessageBubble memoisation', () => {
  it('re-renders only the streaming bubble when a snapshot arrives', () => {
    const view = render(
      <Transcript entries={[answered, streaming('Hel')]} welcome="hi" />,
    )

    expect(markdownRenders).toHaveBeenCalledTimes(1)

    // The store rebuilds every entry object on each frame; only the message identities are stable.
    view.rerender(
      <Transcript
        entries={[{ ...answered }, streaming('Hello there')]}
        welcome="hi"
      />,
    )

    expect(screen.getByText('Hello there')).toBeInTheDocument()
    expect(markdownRenders).toHaveBeenCalledTimes(1)
  })
})
