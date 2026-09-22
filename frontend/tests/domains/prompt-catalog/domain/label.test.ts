import { describe, expect, it } from 'vitest'

import { deriveLabel } from '@/domains/prompt-catalog/domain/label'

describe('deriveLabel', () => {
  it('title-cases a SCREAMING_SNAKE_CASE name (FR-PROMPT-003)', () => {
    expect(deriveLabel('JOB_ID')).toBe('Job Id')
  })

  it('handles a single word', () => {
    expect(deriveLabel('PLATFORM')).toBe('Platform')
  })

  it('handles three or more words', () => {
    expect(deriveLabel('NEW_JOB_ID')).toBe('New Job Id')
  })

  it('collapses repeated underscores instead of producing empty words', () => {
    expect(deriveLabel('JOB__ID')).toBe('Job Id')
  })

  it('returns an empty string for an empty name', () => {
    expect(deriveLabel('')).toBe('')
  })
})
