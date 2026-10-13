import { describe, expect, it } from '@jest/globals'

import { toKnowledgeQuery } from '@/domains/knowledge/application/knowledge-query'

describe('toKnowledgeQuery', () => {
  it('drops blank tags and ignores invalid dates at the route boundary', () => {
    expect(
      toKnowledgeQuery({
        flagged: false,
        offset: 0,
        tags: ' , ',
        from: 'not-a-date',
      }),
    ).toMatchObject({
      tags: [],
      dateFrom: null,
      dateTo: null,
      removalFlagged: false,
    })
  })
})
