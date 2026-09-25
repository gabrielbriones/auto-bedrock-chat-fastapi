import { describe, expect, it } from '@jest/globals'
import { defaultParseSearch, defaultStringifySearch } from '@tanstack/react-router'
import { z } from 'zod'

import { booleanParam, offsetParam, textParam } from '@/app/search-params'

const schema = z.object({
  rating: z.enum(['all', 'positive', 'negative']).catch('all').default('all'),
  tags: textParam,
  flagged: booleanParam(false),
  offset: offsetParam,
})

const validate = (input: Record<string, unknown>) => schema.parse(input)

// The exact codec the router uses, so these assertions describe real URLs.
const roundTrip = (search: Record<string, unknown>) =>
  validate(defaultParseSearch(defaultStringifySearch(search)))

describe('search parameter schemas', () => {
  it('drops an unknown parameter instead of carrying it into route state', () => {
    expect(validate({ rating: 'positive', unknown: 'x' })).toEqual({
      rating: 'positive',
      flagged: false,
      offset: 0,
    })
  })

  it('falls back to the default for a malformed value and keeps the valid ones', () => {
    expect(validate({ rating: 'sideways', offset: -3, tags: 'a,b' })).toEqual({
      rating: 'all',
      tags: 'a,b',
      flagged: false,
      offset: 0,
    })
  })

  it('fills in every default for an empty search', () => {
    expect(validate({})).toEqual({ rating: 'all', flagged: false, offset: 0 })
  })

  it('accepts a hand-typed URL where numbers and booleans arrive as strings', () => {
    expect(validate({ offset: '50', flagged: 'true' })).toEqual({
      rating: 'all',
      flagged: true,
      offset: 50,
    })
  })

  it('rejects a non-boolean string rather than reading it as true', () => {
    expect(validate({ flagged: 'yes' }).flagged).toBe(false)
  })

  it('round-trips a validated search through the router codec unchanged', () => {
    const search = validate({ rating: 'negative', tags: 'latency', flagged: true, offset: 50 })

    expect(roundTrip(search)).toEqual(search)
  })

  it('omits absent text filters from the query string entirely', () => {
    expect(defaultStringifySearch(validate({ offset: 10 }))).toBe(
      '?rating=all&flagged=false&offset=10',
    )
  })
})
