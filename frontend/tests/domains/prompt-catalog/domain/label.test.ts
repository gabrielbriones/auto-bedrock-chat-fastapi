import { describe, expect, it } from '@jest/globals'

import { deriveLabel } from '@/domains/prompt-catalog/domain/label'

describe('deriveLabel', () => {
  it.each([
    ['JOB_ID', 'Job Id'],
    ['PLATFORM', 'Platform'],
    ['NEW_JOB_ID', 'New Job Id'],
  ])('title-cases the SCREAMING_SNAKE_CASE name %s (FR-PROMPT-003)', (name, label) => {
    expect(deriveLabel(name)).toBe(label)
  })

  it('collapses repeated underscores instead of producing empty words', () => {
    expect(deriveLabel('JOB__ID')).toBe('Job Id')
  })

  it('returns an empty string for an empty name', () => {
    expect(deriveLabel('')).toBe('')
  })
})
