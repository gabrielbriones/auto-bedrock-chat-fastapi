import { describe, expect, it } from '@jest/globals'

import { MAX_TAGS, addTag } from '@/shared/kernel/curation'
import { isErr, isOk } from '@/shared/kernel/result'

describe('addTag', () => {
  it('appends a trimmed tag', () => {
    const result = addTag(['emon'], '  ipc  ')

    expect(isOk(result) && result.value).toEqual(['emon', 'ipc'])
  })

  it('rejects a blank candidate', () => {
    const result = addTag([], '   ')

    expect(isErr(result) && result.error).toEqual({ kind: 'empty-tag' })
  })

  it('rejects a tag outside the policy character class', () => {
    const result = addTag([], 'not a tag')

    expect(isErr(result) && result.error).toMatchObject({ kind: 'invalid-tag' })
  })

  it('rejects a tag over the length limit', () => {
    const result = addTag([], 'x'.repeat(33))

    expect(isErr(result) && result.error).toMatchObject({ kind: 'invalid-tag' })
  })

  it('rejects a duplicate', () => {
    const result = addTag(['emon'], 'emon')

    expect(isErr(result) && result.error).toEqual({ kind: 'duplicate-tag', tag: 'emon' })
  })

  it('refuses to exceed the maximum', () => {
    const full = Array.from({ length: MAX_TAGS }, (_, index) => `tag-${index}`)

    expect(isErr(addTag(full, 'one-more'))).toBe(true)
  })

  it('does not mutate the input list', () => {
    const tags = ['emon']
    addTag(tags, 'ipc')

    expect(tags).toEqual(['emon'])
  })
})
