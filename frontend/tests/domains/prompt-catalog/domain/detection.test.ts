import { describe, expect, it } from '@jest/globals'

import { detectBindings } from '@/domains/prompt-catalog/domain/detection'
import { inferPromptVariable, type PromptVariable } from '@/domains/prompt-catalog/domain/prompt-variable'

const withDetection = (name: string, pattern: string, flags = 'i'): PromptVariable => ({
  ...inferPromptVariable(name),
  detection: { pattern, flags },
})

describe('detectBindings', () => {
  it('fills a text variable whose detect pattern matches the message', () => {
    const jobId = withDetection('JOB_ID', '[0-9a-f]{8}')

    expect(detectBindings('please analyze 1a2b3c4d for me', { JOB_ID: jobId }, new Set())).toEqual({
      JOB_ID: { kind: 'text', value: '1a2b3c4d' },
    })
  })

  it('returns nothing when no pattern matches', () => {
    const jobId = withDetection('JOB_ID', '[0-9a-f]{8}')
    expect(detectBindings('no match here', { JOB_ID: jobId }, new Set())).toEqual({})
  })

  it('never overrides a variable the user has already edited explicitly (Phase 2 accept)', () => {
    const jobId = withDetection('JOB_ID', '[0-9a-f]{8}')

    expect(detectBindings('1a2b3c4d', { JOB_ID: jobId }, new Set(['JOB_ID']))).toEqual({})
  })

  it('ignores select, checkbox and number variables even with a detection rule (FR-PROMPT-007b)', () => {
    const select: PromptVariable = { ...withDetection('PLATFORM', 'linux'), inputType: 'select' }
    const checkbox: PromptVariable = { ...withDetection('VERBOSE', 'true'), inputType: 'checkbox' }
    const number: PromptVariable = { ...withDetection('TOP_N', '[0-9]+'), inputType: 'number' }

    expect(detectBindings('linux true 5', { PLATFORM: select, VERBOSE: checkbox, TOP_N: number }, new Set())).toEqual({})
  })

  it('ignores a variable with no detection rule at all', () => {
    const plain = inferPromptVariable('JOB_ID')
    expect(detectBindings('anything', { JOB_ID: plain }, new Set())).toEqual({})
  })

  it('never throws on an uncompilable pattern, and detects nothing for it', () => {
    const broken = withDetection('JOB_ID', '([unclosed')
    expect(detectBindings('anything', { JOB_ID: broken }, new Set())).toEqual({})
  })
})
