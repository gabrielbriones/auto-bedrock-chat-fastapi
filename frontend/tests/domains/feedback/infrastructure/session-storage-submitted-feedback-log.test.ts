import { describe, expect, it } from 'vitest'

import { messageId } from '@/shared/kernel/branded'
import {
  SessionStorageSubmittedFeedbackLog,
  SUBMITTED_FEEDBACK_STORAGE_KEY,
} from '@/domains/feedback/infrastructure/session-storage-submitted-feedback-log'

type MemoryStorage = {
  readonly values: Map<string, string>
  readonly getItem: (key: string) => string | null
  readonly setItem: (key: string, value: string) => void
}

const memoryStorage = (initial: Record<string, string> = {}): MemoryStorage => {
  const values = new Map(Object.entries(initial))
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value)
    },
  }
}

describe('SessionStorageSubmittedFeedbackLog', () => {
  it('loads valid ids and ignores malformed stored values', () => {
    const storage = memoryStorage({
      [SUBMITTED_FEEDBACK_STORAGE_KEY]: JSON.stringify(['m-1', 42, null, 'm-2']),
    })
    const log = new SessionStorageSubmittedFeedbackLog(storage)

    expect([...log.all()]).toEqual([messageId('m-1'), messageId('m-2')])
  })

  it('persists marks and removals as a JSON array', () => {
    const storage = memoryStorage()
    const log = new SessionStorageSubmittedFeedbackLog(storage)

    log.mark(messageId('m-1'))
    log.mark(messageId('m-2'))
    log.unmark(messageId('m-1'))

    expect(storage.values.get(SUBMITTED_FEEDBACK_STORAGE_KEY)).toBe(JSON.stringify(['m-2']))
  })

  it('degrades to memory when storage reads or writes throw', () => {
    const throwingStorage = {
      getItem: () => {
        throw new Error('private mode')
      },
      setItem: () => {
        throw new Error('quota')
      },
    }
    const log = new SessionStorageSubmittedFeedbackLog(throwingStorage)

    log.mark(messageId('m-1'))
    log.unmark(messageId('m-1'))
    log.mark(messageId('m-2'))

    expect([...log.all()]).toEqual([messageId('m-2')])
  })
})