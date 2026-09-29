import type { KbDocumentId } from '@/shared/kernel/branded'
import type { Instant } from '@/shared/kernel/instant'

// DESIGN-001 §6.1. The legacy `buildSynthesisSection` branched on two nullable columns with an
// `if/else if/else` chain, so a fourth server state would have fallen silently into the last
// branch. Modelled as a closed union instead, every reader is forced to be total.
export type SynthesisState =
  | { readonly kind: 'never' }
  | { readonly kind: 'synthesized'; readonly kbDocumentId: KbDocumentId; readonly at: Instant | null }
  | {
      readonly kind: 'rolledBack'
      readonly at: Instant
      readonly by: string | null
      readonly reason: string | null
    }

/** The provenance columns a `FeedbackEntry` carries, already mapped out of the wire shape. */
export type SynthesisProvenance = {
  readonly kbDocumentId: KbDocumentId | null
  readonly integratedAt: Instant | null
  readonly rolledBackAt: Instant | null
  readonly rolledBackBy: string | null
  readonly rollbackReason: string | null
}

export const NEVER_SYNTHESIZED: SynthesisState = { kind: 'never' }

// The integration ID is checked first because the trigger endpoint rejects entries that still
// carry it. Older rows can retain both columns after a rollback, so those rows must remain
// actionable as synthesized rather than exposing a re-synthesis request the server will reject.
export const deriveSynthesisState = (provenance: SynthesisProvenance): SynthesisState => {
  if (provenance.kbDocumentId !== null) {
    return { kind: 'synthesized', kbDocumentId: provenance.kbDocumentId, at: provenance.integratedAt }
  }

  if (provenance.rolledBackAt !== null) {
    return {
      kind: 'rolledBack',
      at: provenance.rolledBackAt,
      by: provenance.rolledBackBy,
      reason: provenance.rollbackReason,
    }
  }

  return NEVER_SYNTHESIZED
}

export type SynthesisStateMatchers<T> = {
  readonly never: () => T
  readonly synthesized: (state: Extract<SynthesisState, { kind: 'synthesized' }>) => T
  readonly rolledBack: (state: Extract<SynthesisState, { kind: 'rolledBack' }>) => T
}

// The mechanism behind "a new server state must fail to compile": `SynthesisStateMatchers` gains a
// required key with the union, so every call site breaks until it handles the new state.
export const matchSynthesisState = <T>(state: SynthesisState, matchers: SynthesisStateMatchers<T>): T => {
  switch (state.kind) {
    case 'never':
      return matchers.never()
    case 'synthesized':
      return matchers.synthesized(state)
    case 'rolledBack':
      return matchers.rolledBack(state)
  }
}

/** FR-REV-016: the batch runner's phase, which gates the per-entry synthesise action. */
export type SynthesisPhase = 'idle' | 'running' | 'completed' | 'failed'

/** A batch run in progress would make a per-entry trigger race it into an unexplained 409. */
export const isSynthesisPhaseSafe = (phase: SynthesisPhase): boolean => phase !== 'running'
