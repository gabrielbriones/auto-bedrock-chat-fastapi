import { describe, expect, it } from 'vitest'

import { messageId, turnId } from '@/shared/kernel/branded'
import { Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

import { createMessage } from '@/domains/messaging/domain/message'
import { renderTranscript } from '@/domains/messaging/domain/transcript'
import { answerTurn, abandonTurn, applyStreamingSnapshot, createTurn, failTurn } from '@/domains/messaging/domain/turn'

const at = (iso: string): Instant => {
  const instant = Instant.fromIso(iso)
  if (!isOk(instant)) {
    throw new Error(`unparseable fixture instant: ${iso}`)
  }

  return instant.value
}

const answered = (id: string, askedAt: string, answeredAt: string) => {
  const turn = createTurn(turnId(id), createMessage('user', 'ping', at(askedAt), 0))
  const result = answerTurn(
    turn,
    createMessage('assistant', 'pong', at(answeredAt), 1, messageId(`${id}-m`)),
  )

  if (!isOk(result)) {
    throw new Error('fixture turn should answer')
  }

  return result.value
}

describe('renderTranscript', () => {
  it('keeps persisted history in server order ahead of anything live, whatever its timestamps say', () => {
    const history = [
      { message: createMessage('user', 'first question', at('2026-08-28T12:00:00Z'), 1), activity: null },
      { message: createMessage('assistant', 'first answer', at('2026-08-28T10:00:01Z'), 2), activity: null },
      { message: createMessage('user', 'second question', at('2026-08-28T12:00:00Z'), 3), activity: null },
      { message: createMessage('assistant', 'second answer', at('2026-08-28T10:00:03Z'), 4), activity: null },
    ]
    const live = answered('t1', '2026-08-28T09:00:00Z', '2026-08-28T09:00:01Z')

    expect(renderTranscript([live], [], history).map((entry) => entry.message.raw)).toEqual([
      'first question',
      'first answer',
      'second question',
      'second answer',
      'ping',
      'pong',
    ])
  })

  it('emits the request and, once resolved, the response of every turn', () => {
    const turn = answered('t1', '2026-08-28T10:00:00Z', '2026-08-28T10:00:02Z')

    expect(renderTranscript([turn], []).map((entry) => entry.key)).toEqual([
      't1:request',
      't1:response',
    ])
  })

  it('marks an unresolved turn\u2019s request as pending and omits a response', () => {
    const turn = createTurn(turnId('t1'), createMessage('user', 'ping', at('2026-08-28T10:00:00Z'), 0))
    const entries = renderTranscript([turn], [])

    expect(entries).toHaveLength(1)
    expect(entries[0]?.pending).toBe(true)
  })

  it('interleaves transient notices by timestamp rather than appending them', () => {
    const first = answered('t1', '2026-08-28T10:00:00Z', '2026-08-28T10:00:02Z')
    const second = answered('t2', '2026-08-28T10:00:06Z', '2026-08-28T10:00:08Z')
    const notice = {
      id: 'transient-1',
      message: createMessage('system', 'Reconnected', at('2026-08-28T10:00:04Z'), 4),
    }

    expect(renderTranscript([first, second], [notice]).map((entry) => entry.key)).toEqual([
      't1:request',
      't1:response',
      'transient-1',
      't2:request',
      't2:response',
    ])
  })

  // ADR-013: only turns are persistable, so rehydrating a conversation must not resurrect
  // "Connection error occurred" as though the assistant had said it.
  it('drops transient notices when the transcript is rebuilt from turns alone', () => {
    const turn = answered('t1', '2026-08-28T10:00:00Z', '2026-08-28T10:00:02Z')
    const notice = {
      id: 'transient-1',
      message: createMessage('system', 'Connection error occurred', at('2026-08-28T10:00:03Z'), 3),
    }

    const withNotice = renderTranscript([turn], [notice])
    const reloaded = renderTranscript([turn], [])

    expect(withNotice.some((entry) => entry.transient)).toBe(true)
    expect(reloaded.some((entry) => entry.transient)).toBe(false)
    expect(reloaded.map((entry) => entry.key)).toEqual(['t1:request', 't1:response'])
  })

  // FR-MSG-005 vs ADR-004: a frame that never left is not the same event as one whose answer the
  // connection swallowed, and only the latter earns a bubble.
  it('shows an interruption for a lost connection but not for a frame that never left', () => {
    const start = createTurn(turnId('t1'), createMessage('user', 'ping', at('2026-08-28T10:00:00Z'), 0))
    const streaming = applyStreamingSnapshot(start, 'IPC is', 'text')
    if (!isOk(streaming)) {
      throw new Error('fixture turn should stream')
    }

    const lost = abandonTurn(streaming.value, 'connection-lost')
    const neverSent = abandonTurn(start, 'never-sent')
    if (!isOk(lost) || !isOk(neverSent)) {
      throw new Error('fixture turn should abandon')
    }

    const interrupted = renderTranscript([lost.value], [])
    expect(interrupted.map((entry) => entry.key)).toEqual(['t1:request', 't1:interrupted'])
    expect(interrupted[1]?.message.raw).toBe('IPC is')

    expect(renderTranscript([neverSent.value], []).map((entry) => entry.key)).toEqual(['t1:request'])
  })

  it('orders messages sharing a timestamp by insertion sequence', () => {
    const shared = '2026-08-28T10:00:00Z'
    const turn = createTurn(turnId('t1'), createMessage('user', 'ping', at(shared), 0))
    const resolved = failTurn(turn, createMessage('assistant', 'boom', at(shared), 1))

    if (!isOk(resolved)) {
      throw new Error('fixture turn should fail')
    }

    expect(renderTranscript([resolved.value], []).map((entry) => entry.message.raw)).toEqual([
      'ping',
      'boom',
    ])
  })

  it('shows the cumulative streaming snapshot as its own entry, not the request or response (FR-MSG-006)', () => {
    const turn = createTurn(turnId('t1'), createMessage('user', 'ping', at('2026-08-28T10:00:00Z'), 0))
    const streaming = applyStreamingSnapshot(turn, 'Hello wor', 'text')
    if (!isOk(streaming)) {
      throw new Error('fixture turn should stream')
    }

    const entries = renderTranscript([streaming.value], [])

    expect(entries.map((entry) => [entry.key, entry.message.raw, entry.streaming, entry.progress])).toEqual([
      ['t1:request', 'ping', true, false],
      ['t1:activity', 'Hello wor', true, false],
    ])
    expect(entries[1]?.pending).toBe(true)
  })

  it('renders a tool-progress notice distinctly from streamed assistant text', () => {
    const turn = createTurn(turnId('t1'), createMessage('user', 'ping', at('2026-08-28T10:00:00Z'), 0))
    const progress = applyStreamingSnapshot(turn, 'Calling extract_job_metrics... (1/3)', 'tool-progress')
    if (!isOk(progress)) {
      throw new Error('fixture turn should stream')
    }

    const entries = renderTranscript([progress.value], [])

    expect(entries[1]).toMatchObject({
      key: 't1:activity',
      progress: true,
      message: expect.objectContaining({ raw: 'Calling extract_job_metrics... (1/3)' }),
    })
  })
})
