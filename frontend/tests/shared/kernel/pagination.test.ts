import { describe, expect, it } from 'vitest'

import { DEFAULT_PAGE_LIMIT, offsetWindow } from '@/shared/kernel/pagination'

describe('offsetWindow', () => {
  it('reports a one-based range over the current page', () => {
    expect(offsetWindow({ limit: 50, offset: 0 }, 128)).toMatchObject({ from: 1, to: 50, total: 128 })
    expect(offsetWindow({ limit: 50, offset: 100 }, 128)).toMatchObject({ from: 101, to: 128 })
  })

  it('offers no navigation for a single full page', () => {
    const range = offsetWindow({ limit: 50, offset: 0 }, 12)

    expect(range.hasPrevious).toBe(false)
    expect(range.hasNext).toBe(false)
  })

  // FIX-06: the defect this primitive exists to close.
  it('still offers a way back from a page that has been emptied', () => {
    const range = offsetWindow({ limit: 50, offset: 100 }, 0)

    expect(range).toMatchObject({ from: 0, to: 0, total: 0, hasPrevious: true, previousOffset: 50 })
  })

  it('does not offer a way back from the first page of an empty list', () => {
    expect(offsetWindow({ limit: 50, offset: 0 }, 0).hasPrevious).toBe(false)
  })

  it('never proposes a negative previous offset', () => {
    expect(offsetWindow({ limit: 50, offset: 20 }, 200).previousOffset).toBe(0)
  })

  it('treats a non-positive limit and a negative offset as their defaults', () => {
    expect(offsetWindow({ limit: 0, offset: -10 }, 60)).toMatchObject({
      from: 1,
      to: DEFAULT_PAGE_LIMIT,
      hasPrevious: false,
    })
  })

  it('clamps a negative total to zero', () => {
    expect(offsetWindow({ limit: 50, offset: 0 }, -1).total).toBe(0)
  })
})
