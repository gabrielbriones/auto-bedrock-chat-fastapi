import { act, render } from '@testing-library/react'
import { describe, expect, it, jest } from '@jest/globals'

import { Instant } from '@/shared/kernel/instant'
import { turnId } from '@/shared/kernel/branded'
import { isOk } from '@/shared/kernel/result'
import { createMessage } from '@/domains/messaging/domain/message'
import { renderTranscript } from '@/domains/messaging/domain/transcript'
import {
  answerTurn,
  applyStreamingSnapshot,
  createTurn,
  type Turn,
} from '@/domains/messaging/domain/turn'

// T-084 / NFR-PERF-005. The budget is a render count first and a clock second: counts are exact and
// CI-stable, and every regression this benchmark exists to catch (a lost `memo`, an inline prop, a
// context read that widens) shows up as one. The wall clock is the coarse backstop underneath.
const markdownRenders = jest.fn()

// No `jest.mock` hoisting under native ESM: register the mock, then import the subject.
jest.unstable_mockModule('@/domains/messaging/presentation/MarkdownView', () => ({
  MarkdownView: ({ content }: { readonly content: string }) => {
    markdownRenders(content)
    return <span>{content}</span>
  },
}))

const { Transcript } = await import('@/domains/messaging/presentation/Transcript')

const FRAMES = 100
/** Turns, not entries: each answers into a request and a response, and 49 of them keep the
 * transcript just under the virtualisation threshold so the whole list stays live in the DOM. */
const BACKLOG = 49
/** One 60 fps frame per snapshot, for a backlog no virtualiser is helping with. */
const BUDGET_MS_PER_FRAME = 16

const instant = Instant.fromEpochMilliseconds(0)
if (!isOk(instant)) {
  throw new Error('Expected the benchmark instant to be valid')
}
const at = instant.value

const settled = (seq: number): Turn => {
  const turn = createTurn(turnId(`turn-${seq}`), createMessage('user', `question ${seq}`, at, seq))
  const answered = answerTurn(turn, createMessage('assistant', `answer ${seq}`, at, seq + 1))

  if (!isOk(answered)) {
    throw new Error('Expected the backlog turn to answer')
  }

  return answered.value
}

const snapshot = (turn: Turn, text: string): Turn => {
  const applied = applyStreamingSnapshot(turn, text, 'text')

  if (!isOk(applied)) {
    throw new Error('Expected the snapshot to apply')
  }

  return applied.value
}

describe('streaming performance (NFR-PERF-005)', () => {
  it(`applies ${FRAMES} snapshots without re-rendering the message list`, () => {
    const backlog = Array.from({ length: BACKLOG }, (_, index) => settled(index))
    let live = createTurn(turnId('live'), createMessage('user', 'stream this', at, BACKLOG * 2))

    const view = render(
      <Transcript
        entries={renderTranscript([...backlog, live], [])}
        welcome="hi"
      />,
    )

    // One parse per settled turn's question and answer, plus the live turn's question.
    // Nothing below may add to this.
    const afterMount = markdownRenders.mock.calls.length
    expect(afterMount).toBe(BACKLOG * 2 + 1)

    const started = performance.now()

    act(() => {
      for (let frame = 1; frame <= FRAMES; frame += 1) {
        live = snapshot(live, 'token '.repeat(frame))

        view.rerender(
          <Transcript
            entries={renderTranscript([...backlog, live], [])}
            welcome="hi"
          />,
        )
      }
    })

    const msPerFrame = (performance.now() - started) / FRAMES

    // FIX-17's whole point: a streaming snapshot renders plain text, so it parses no markdown and
    // touches no other bubble. The one exception is the request itself, which re-renders (still as
    // Markdown, FIX-01) exactly once when the turn moves from pending to streaming.
    expect(markdownRenders.mock.calls.length).toBe(afterMount + 1)

    const answered = answerTurn(live, createMessage('assistant', 'final answer', at, 1))
    if (!isOk(answered)) {
      throw new Error('Expected the live turn to answer')
    }

    view.rerender(
      <Transcript
        entries={renderTranscript([...backlog, answered.value], [])}
        welcome="hi"
      />,
    )

    // At most twice per turn: the plain-text→markdown swap is the only parse the turn ever pays for.
    expect(markdownRenders.mock.calls.length - afterMount - 1).toBeLessThanOrEqual(2)
    expect(msPerFrame).toBeLessThan(BUDGET_MS_PER_FRAME)
  })
})
