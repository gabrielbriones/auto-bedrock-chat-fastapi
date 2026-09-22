import { messageId, type MessageId } from '@/shared/kernel/branded'

import type { SubmittedFeedbackLog } from '@/domains/feedback/application/ports'

export const SUBMITTED_FEEDBACK_STORAGE_KEY = 'feedback.submitted'

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

const browserStorage = (): StorageLike | null => {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

const readIds = (storage: StorageLike | null): Set<MessageId> => {
  if (storage === null) {
    return new Set()
  }

  try {
    const raw = storage.getItem(SUBMITTED_FEEDBACK_STORAGE_KEY)
    if (raw === null) {
      return new Set()
    }

    const parsed: unknown = JSON.parse(raw)
    return new Set(
      Array.isArray(parsed)
        ? parsed.filter((value): value is string => typeof value === 'string').map(messageId)
        : [],
    )
  } catch {
    return new Set()
  }
}

export class SessionStorageSubmittedFeedbackLog implements SubmittedFeedbackLog {
  readonly #ids: Set<MessageId>
  #storage: StorageLike | null

  constructor(storage: StorageLike | null = browserStorage()) {
    this.#storage = storage
    this.#ids = readIds(storage)
  }

  all(): ReadonlySet<MessageId> {
    return this.#ids
  }

  mark(id: MessageId): void {
    this.#ids.add(id)
    this.#persist()
  }

  unmark(id: MessageId): void {
    this.#ids.delete(id)
    this.#persist()
  }

  #persist(): void {
    if (this.#storage === null) {
      return
    }

    try {
      this.#storage.setItem(SUBMITTED_FEEDBACK_STORAGE_KEY, JSON.stringify([...this.#ids]))
    } catch {
      this.#storage = null
    }
  }
}