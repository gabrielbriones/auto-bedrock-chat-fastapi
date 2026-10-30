import { describe, expect, it } from '@jest/globals'

import { turnId } from '@/shared/kernel/branded'
import { Instant } from '@/shared/kernel/instant'
import { isErr, isOk } from '@/shared/kernel/result'

import { createMessage } from '@/domains/messaging/domain/message'
import {
  abandonTurn,
  answerTurn,
  applyStreamingSnapshot,
  classifyTypingFrame,
  createTurn,
  failTurn,
  finishLiveTool,
  isUnresolved,
  startLiveTool,
  type Turn,
  type TurnStatus,
} from '@/domains/messaging/domain/turn'

const anInstant = (): Instant => {
  const instant = Instant.fromIso('2026-08-28T10:00:00Z')
  if (!isOk(instant)) {
    throw new Error('unparseable fixture instant')
  }

  return instant.value
}

const aTurn = (): Turn =>
  createTurn(turnId('t1'), createMessage('user', 'analyse job 42', anInstant(), 0))

const aResponse = () => createMessage('assistant', 'here you go', anInstant(), 1)

const unwrap = (result: ReturnType<typeof answerTurn>): Turn => {
  if (!isOk(result)) {
    throw new Error(`expected an ok result, got ${JSON.stringify(result)}`)
  }

  return result.value
}

describe('Turn', () => {
  it('starts sent, unresolved, with a request and no response (I1, I2)', () => {
    const turn = aTurn()

    expect(turn.status).toBe('sent')
    expect(turn.response).toBeNull()
    expect(isUnresolved(turn)).toBe(true)
  })

  it.each([
    ['answered', answerTurn],
    ['failed', failTurn],
  ] as const)('attaches a response when resolving to %s (I2)', (status, resolve) => {
    const result = resolve(aTurn(), aResponse())

    expect(isOk(result) && result.value.status).toBe(status)
    expect(isOk(result) && result.value.response?.raw).toBe('here you go')
  })

  it('abandons an unresolved turn without inventing a response (FR-MSG-024)', () => {
    const result = abandonTurn(aTurn())

    expect(isOk(result) && result.value.status).toBe('abandoned')
    expect(isOk(result) && result.value.response).toBeNull()
  })

  it('carries the conversation id discovered on the answer', () => {
    const result = answerTurn(aTurn(), aResponse(), 'conv-1' as never)

    expect(isOk(result) && result.value.conversationId).toBe('conv-1')
  })

  // I3, exhaustively: every `TurnStatus` pair is exercised, not just the paths above.
  describe('transition table (I3)', () => {
    const ALL_STATUSES: readonly TurnStatus[] = [
      'composing',
      'sent',
      'streaming',
      'answered',
      'failed',
      'abandoned',
    ]
    const UNRESOLVED_STATUSES: readonly TurnStatus[] = ['sent', 'streaming']

    const withStatus = (status: TurnStatus): Turn => ({ ...aTurn(), status })

    it.each(ALL_STATUSES)('from %s', (from) => {
      const turn = withStatus(from)
      const expectLegal = UNRESOLVED_STATUSES.includes(from)

      for (const result of [
        applyStreamingSnapshot(turn, 'hi', 'text'),
        answerTurn(turn, aResponse()),
        failTurn(turn, aResponse()),
        abandonTurn(turn),
      ]) {
        if (expectLegal) {
          expect(isOk(result)).toBe(true)
        } else {
          expect(isErr(result) && result.error).toEqual({
            kind: 'illegal-transition',
            from,
            to: expect.any(String),
          })
        }
      }
    })
  })

  describe('classifyTypingFrame (ADR-008)', () => {
    it.each([
      ['Calling extract_job_metrics... (1/3)', 'tool-progress'],
      ['Calling a_tool_with_underscores... (2/2)', 'tool-progress'],
      ['AI is thinking...', 'tool-progress'],
      ['Hello world', 'text'],
      ['Calling something without the trailer', 'text'],
      ['', 'text'],
    ] as const)('classifies %j as %s', (text, kind) => {
      expect(classifyTypingFrame(text)).toBe(kind)
    })
  })

  describe('applyStreamingSnapshot (FR-MSG-006/006a, ADR-008)', () => {
    it('replaces the assistant text with each cumulative snapshot, never appending', () => {
      const first = unwrap(applyStreamingSnapshot(aTurn(), 'Hel', 'text'))
      const second = unwrap(applyStreamingSnapshot(first, 'Hello', 'text'))
      const third = unwrap(applyStreamingSnapshot(second, 'Hello world', 'text'))

      expect(third.streamText).toBe('Hello world')
      expect(third.status).toBe('streaming')
    })

    it('drops a duplicate or out-of-order snapshot, keeping the final text stable', () => {
      const grown = unwrap(applyStreamingSnapshot(aTurn(), 'Hello world', 'text'))
      const replay = unwrap(applyStreamingSnapshot(grown, 'Hel', 'text'))

      expect(replay.streamText).toBe('Hello world')
    })

    it('does not append a tool-progress frame to the assistant text', () => {
      const progress = unwrap(
        applyStreamingSnapshot(aTurn(), 'Calling extract_job_metrics... (1/3)', 'tool-progress'),
      )

      expect(progress.toolProgress).toBe('Calling extract_job_metrics... (1/3)')
      expect(progress.streamText).toBeNull()
    })

    it('clears a stale tool-progress notice once real text resumes', () => {
      const progress = unwrap(
        applyStreamingSnapshot(aTurn(), 'Calling extract_job_metrics... (1/3)', 'tool-progress'),
      )
      const resumed = unwrap(applyStreamingSnapshot(progress, 'Job 42 uses 2.1 IPC', 'text'))

      expect(resumed.toolProgress).toBeNull()
      expect(resumed.streamText).toBe('Job 42 uses 2.1 IPC')
    })

    it('tracks each live tool call from start to finish, in start order', () => {
      const started = unwrap(startLiveTool(aTurn(), { id: 'a', name: 'get_jobs', arguments: { q: 1 } }))
      const both = unwrap(startLiveTool(started, { id: 'b', name: 'get_job', arguments: {} }))
      const finished = unwrap(finishLiveTool(both, 'b', 'HTTP 404', 12))

      expect(finished.status).toBe('streaming')
      expect(finished.liveTools).toEqual([
        { id: 'a', name: 'get_jobs', arguments: { q: 1 }, status: 'running', error: null, durationMs: null },
        { id: 'b', name: 'get_job', arguments: {}, status: 'failed', error: 'HTTP 404', durationMs: 12 },
      ])
    })

    it('keeps the watched durations on the answer\'s activity', () => {
      const started = unwrap(startLiveTool(aTurn(), { id: 'a', name: 'get_jobs', arguments: {} }))
      const done = unwrap(finishLiveTool(started, 'a', null, 250))
      const answered = unwrap(answerTurn(done, aResponse()))

      expect(answered.activity.toolDurationsMs).toEqual({ a: 250 })
    })

    it('ignores a duplicate start frame for a call it already tracks', () => {
      const started = unwrap(startLiveTool(aTurn(), { id: 'a', name: 'get_jobs', arguments: {} }))
      const done = unwrap(finishLiveTool(started, 'a', null, 5))
      const replay = unwrap(startLiveTool(done, { id: 'a', name: 'get_jobs', arguments: {} }))

      expect(replay.liveTools).toEqual(done.liveTools)
    })

    it('resets the text baseline when a tool round starts, so the next round can stream from empty', () => {
      const preamble = unwrap(applyStreamingSnapshot(aTurn(), 'Let me look that up for you.', 'text'))
      const tool = unwrap(startLiveTool(preamble, { id: 'a', name: 'get_jobs', arguments: {} }))
      const next = unwrap(applyStreamingSnapshot(tool, 'Two', 'text'))

      expect(tool.streamText).toBeNull()
      expect(next.streamText).toBe('Two')
    })

    it('drops the legacy "Calling…" notice once tool calls are tracked individually', () => {
      const notice = unwrap(applyStreamingSnapshot(aTurn(), 'Calling get_jobs... (1/2)', 'tool-progress'))
      const tool = unwrap(startLiveTool(notice, { id: 'a', name: 'get_jobs', arguments: {} }))
      const second = unwrap(applyStreamingSnapshot(tool, 'Calling get_job... (2/2)', 'tool-progress'))

      expect(tool.toolProgress).toBeNull()
      expect(second.toolProgress).toBeNull()
    })

    it('rejects a snapshot for a turn outside {sent, streaming} (I4)', () => {
      const answered = unwrap(answerTurn(aTurn(), aResponse()))
      const result = applyStreamingSnapshot(answered, 'late', 'text')

      expect(isErr(result) && result.error).toEqual({
        kind: 'illegal-transition',
        from: 'answered',
        to: 'streaming',
      })
    })
  })
})
