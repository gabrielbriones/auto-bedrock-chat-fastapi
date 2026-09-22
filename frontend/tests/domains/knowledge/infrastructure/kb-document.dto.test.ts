import { describe, expect, it } from 'vitest'

import { invalidResponseProblem } from '@/shared/http/exception'
import { CalendarDate } from '@/shared/kernel/instant'
import { isErr, isOk } from '@/shared/kernel/result'
import {
  fromSparsePatch,
  toKbDocument,
  toKbDocumentPage,
  toRollbackResult,
} from '@/domains/knowledge/infrastructure/dto/kb-document.dto'

describe('knowledge document DTOs', () => {
  it('normalises an omitted document payload to nullable defaults', () => {
    const result = toKbDocument({ id: 'kb/1' })

    expect(isOk(result) && result.value).toMatchObject({
      id: 'kb/1',
      title: null,
      source: null,
      topic: null,
      content: null,
      datePublished: null,
      metadata: {},
      tags: [],
      chunkCount: null,
      createdAt: null,
      credibility: { score: 1, removalFlagged: false, band: 'good' },
    })
  })

  it('reports invalid document dates and rollback payloads', () => {
    const document = toKbDocument({ id: 'kb/1', date_published: 'tomorrow' })
    const rollback = toRollbackResult({ article_id: 'kb/1', rolled_back_at: 'yesterday' })

    expect(isErr(document) && document.error.code).toBe('invalid-response')
    expect(isErr(rollback) && rollback.error.code).toBe('invalid-response')
  })

  it('maps an empty document page', () => {
    const result = toKbDocumentPage({ items: [], total: 0, limit: 50, offset: 0 })

    expect(isOk(result) && result.value).toEqual({ items: [], total: 0, limit: 50, offset: 0 })
  })

  it('serialises only supplied patch fields and preserves a date value', () => {
    const date = CalendarDate.fromIso('2026-09-10')

    expect(isOk(date)).toBe(true)
    expect(fromSparsePatch({})).toEqual({})
    expect(fromSparsePatch({ datePublished: isOk(date) ? date.value : null })).toEqual({
      date_published: '2026-09-10',
    })
  })

  it('keeps the invalid-response problem shape for malformed payloads', () => {
    expect(isErr(toKbDocument({}))).toBe(true)
    expect(invalidResponseProblem('invalid', []).code).toBe('invalid-response')
  })
})
