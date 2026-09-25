import { describe, expect, it } from '@jest/globals'

import { MAX_TAGS, addTag } from '@/shared/kernel/curation'
import { isErr, isOk } from '@/shared/kernel/result'

describe('addTag', () => {
  it('appends a trimmed tag', () => {
    const result = addTag(['emon'], '  ipc  ')

    expect(isOk(result) && result.value).toEqual(['emon', 'ipc'])
  })

  it.each<[string, readonly string[], string, Record<string, unknown>]>([
    ['a blank candidate', [], '   ', { kind: 'empty-tag' }],
    ['a tag outside the policy character class', [], 'not a tag', { kind: 'invalid-tag' }],
    ['a tag over the length limit', [], 'x'.repeat(33), { kind: 'invalid-tag' }],
    ['a duplicate', ['emon'], 'emon', { kind: 'duplicate-tag', tag: 'emon' }],
    [
      'one more than the maximum',
      Array.from({ length: MAX_TAGS }, (_, index) => `tag-${index}`),
      'one-more',
      { kind: 'too-many-tags' },
    ],
  ])('rejects %s', (_label, existing, candidate, error) => {
    const result = addTag(existing, candidate)

    expect(isErr(result) && result.error).toMatchObject(error)
  })

  it('does not mutate the input list', () => {
    const tags = ['emon']
    addTag(tags, 'ipc')

    expect(tags).toEqual(['emon'])
  })
})
