import { describe, expect, it } from '@jest/globals'

import { kbDocumentId } from '@/shared/kernel/branded'
import {
  deriveSynthesisState,
  isSynthesisPhaseSafe,
  matchSynthesisState,
  type SynthesisProvenance,
} from '@/domains/review/domain/public'

import { anInstant } from './feedback-entry.fixture'

const noProvenance: SynthesisProvenance = {
  kbDocumentId: null,
  integratedAt: null,
  rolledBackAt: null,
  rolledBackBy: null,
  rollbackReason: null,
}

describe('deriveSynthesisState', () => {
  it('is `never` for an entry that was never promoted', () => {
    expect(deriveSynthesisState(noProvenance)).toEqual({ kind: 'never' })
  })

  it('is `synthesized` when a KB document exists', () => {
    const at = anInstant('2026-09-03T11:05:12Z')

    expect(
      deriveSynthesisState({ ...noProvenance, kbDocumentId: kbDocumentId('kb-902'), integratedAt: at }),
    ).toEqual({ kind: 'synthesized', kbDocumentId: 'kb-902', at })
  })

  it('tolerates a synthesized entry whose integration timestamp is missing', () => {
    const state = deriveSynthesisState({ ...noProvenance, kbDocumentId: kbDocumentId('kb-902') })

    expect(state).toMatchObject({ kind: 'synthesized', at: null })
  })

  // The trigger endpoint rejects an entry that still carries an integration ID, so the UI must
  // keep showing the existing article rather than offering an invalid re-synthesis action.
  it('is `synthesized` when a legacy row carries both columns', () => {
    const at = anInstant('2026-09-05T14:22:07Z')
    const state = deriveSynthesisState({
      kbDocumentId: kbDocumentId('kb-902'),
      integratedAt: anInstant('2026-09-03T11:05:12Z'),
      rolledBackAt: at,
      rolledBackBy: 'mokoye',
      rollbackReason: 'Superseded.',
    })

    expect(state).toEqual({ kind: 'synthesized', kbDocumentId: 'kb-902', at: anInstant('2026-09-03T11:05:12Z') })
  })
})

describe('matchSynthesisState', () => {
  const label = (provenance: SynthesisProvenance): string =>
    matchSynthesisState(deriveSynthesisState(provenance), {
      never: () => 'never',
      synthesized: (state) => `synthesized:${state.kbDocumentId}`,
      rolledBack: (state) => `rolledBack:${state.by ?? 'unknown'}`,
    })

  it('is total over every state', () => {
    expect(label(noProvenance)).toBe('never')
    expect(label({ ...noProvenance, kbDocumentId: kbDocumentId('kb-1') })).toBe('synthesized:kb-1')
    expect(label({ ...noProvenance, rolledBackAt: anInstant('2026-09-05T14:22:07Z') })).toBe(
      'rolledBack:unknown',
    )
  })
})

describe('isSynthesisPhaseSafe', () => {
  // FR-REV-016 / BC-007: a batch run in flight is the one phase that must disable the action.
  it('refuses only while a batch run is in flight', () => {
    expect(isSynthesisPhaseSafe('running')).toBe(false)
    expect(isSynthesisPhaseSafe('idle')).toBe(true)
    expect(isSynthesisPhaseSafe('completed')).toBe(true)
    expect(isSynthesisPhaseSafe('failed')).toBe(true)
  })
})
