import { CalendarDate } from '@/shared/kernel/instant'
import { DEFAULT_PAGE_LIMIT } from '@/shared/kernel/pagination'
import { isOk } from '@/shared/kernel/result'

import type { KbQuery } from '@/domains/knowledge/application/ports'

export type KnowledgeSearchInput = {
  readonly source?: string | undefined
  readonly topic?: string | undefined
  readonly tags?: string | undefined
  readonly from?: string | undefined
  readonly to?: string | undefined
  readonly flagged: boolean
  readonly offset: number
}

const date = (value: string | undefined): CalendarDate | null => {
  if (value === undefined) {
    return null
  }

  const parsed = CalendarDate.fromIso(value)
  return isOk(parsed) ? parsed.value : null
}

export const toKnowledgeQuery = (search: KnowledgeSearchInput): KbQuery => ({
  source: search.source ?? null,
  topic: search.topic ?? null,
  tags: search.tags?.split(',').map((tag) => tag.trim()).filter(Boolean) ?? [],
  dateFrom: date(search.from),
  dateTo: date(search.to),
  removalFlagged: search.flagged,
  limit: DEFAULT_PAGE_LIMIT,
  offset: search.offset,
})