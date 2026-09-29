import { Button } from '@/components/ui/button'
import { REVIEW_COPY } from '@/shared/copy/review'
import type { KbDocumentId } from '@/shared/kernel/branded'

import type { SynthesisProblem } from '@/domains/review/application/review.store'
import type { FeedbackEntry, SynthesisPhase, SynthesisState } from '@/domains/review/domain/public'
import { isSynthesisEligible, isSynthesisPhaseSafe, matchSynthesisState } from '@/domains/review/domain/public'
import { Section } from '@/domains/review/presentation/ReviewDrawer'

export type SynthesisSectionProps = {
  readonly entry: FeedbackEntry
  readonly phase: SynthesisPhase | null
  readonly pending: boolean
  readonly problem: SynthesisProblem | null
  readonly onSynthesize: (id: FeedbackEntry['id']) => void
  readonly onRollback: (kbDocumentId: KbDocumentId) => void
}

// FR-REV-016: an unknown phase (the status probe failed) is not a safe phase — the action stays
// disabled until the app can tell whether a batch run is active.
const phaseHint = (phase: SynthesisPhase | null): string | null => {
  if (phase === null) return REVIEW_COPY.synthesis.phaseUnknownHint
  return isSynthesisPhaseSafe(phase) ? null : REVIEW_COPY.synthesis.phaseRunningHint
}

const problemMessage = (problem: SynthesisProblem | null): string | null => {
  if (problem === null) {
    return null
  }

  const { kind, problem: cause } = problem

  if (kind === 'synthesize') {
    if (cause.status === 409) return REVIEW_COPY.synthesis.synthesizeConflict
    if (cause.status === 422) return cause.detail ?? REVIEW_COPY.synthesis.synthesizeValidation
    return REVIEW_COPY.synthesis.genericFailure
  }

  if (cause.status === 422) return REVIEW_COPY.synthesis.rollbackNotSynthesized
  if (cause.status === 500) return REVIEW_COPY.synthesis.rollbackServerError
  return REVIEW_COPY.synthesis.genericFailure
}

const formatWhen = (at: FeedbackEntry['createdAt']) => new Date(at.epochMilliseconds).toLocaleString()

type NeverProps = {
  readonly entryId: FeedbackEntry['id']
  readonly phase: SynthesisPhase | null
  readonly pending: boolean
  readonly onSynthesize: SynthesisSectionProps['onSynthesize']
}

function Never({ entryId, phase, pending, onSynthesize }: NeverProps) {
  const hint = phaseHint(phase)
  return (
    <div className="grid gap-2 text-sm">
      <p className="text-muted-foreground">{REVIEW_COPY.synthesis.neverHint}</p>
      {hint === null ? null : <p className="text-muted-foreground">{hint}</p>}
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={pending || hint !== null}
        onClick={() => onSynthesize(entryId)}
      >
        {REVIEW_COPY.synthesis.trigger}
      </Button>
    </div>
  )
}

type SynthesizedProps = {
  readonly state: Extract<SynthesisState, { kind: 'synthesized' }>
  readonly pending: boolean
  readonly onRollback: SynthesisSectionProps['onRollback']
}

function Synthesized({ state, pending, onRollback }: SynthesizedProps) {
  return (
    <div className="grid gap-2 text-sm">
      <p>{REVIEW_COPY.synthesis.synthesizedStatus(state.kbDocumentId)}</p>
      {state.at === null ? null : <p className="text-muted-foreground">{formatWhen(state.at)}</p>}
      <Button
        type="button"
        variant="destructive"
        size="sm"
        disabled={pending}
        onClick={() => onRollback(state.kbDocumentId)}
      >
        {REVIEW_COPY.synthesis.rollback}
      </Button>
    </div>
  )
}

type RolledBackProps = {
  readonly entryId: FeedbackEntry['id']
  readonly state: Extract<SynthesisState, { kind: 'rolledBack' }>
  readonly phase: SynthesisPhase | null
  readonly pending: boolean
  readonly onSynthesize: SynthesisSectionProps['onSynthesize']
}

function RolledBack({ entryId, state, phase, pending, onSynthesize }: RolledBackProps) {
  const hint = phaseHint(phase)
  return (
    <div className="grid gap-2 text-sm">
      <p>{REVIEW_COPY.drawer.rolledBack(state.by, state.reason)}</p>
      <p className="text-muted-foreground">{formatWhen(state.at)}</p>
      {hint === null ? null : <p className="text-muted-foreground">{hint}</p>}
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={pending || hint !== null}
        onClick={() => onSynthesize(entryId)}
      >
        {REVIEW_COPY.synthesis.retrigger}
      </Button>
    </div>
  )
}

// FR-REV-015: exhaustive over `SynthesisState` — a new server-reported state fails to compile
// here rather than silently rendering nothing.
export function SynthesisSection(props: SynthesisSectionProps) {
  if (!isSynthesisEligible(props.entry)) {
    return null
  }

  const body = matchSynthesisState(props.entry.synthesis, {
    never: () => (
      <Never entryId={props.entry.id} phase={props.phase} pending={props.pending} onSynthesize={props.onSynthesize} />
    ),
    synthesized: (state) => <Synthesized state={state} pending={props.pending} onRollback={props.onRollback} />,
    rolledBack: (state) => (
      <RolledBack
        entryId={props.entry.id}
        state={state}
        phase={props.phase}
        pending={props.pending}
        onSynthesize={props.onSynthesize}
      />
    ),
  })

  const message = problemMessage(props.problem)

  return (
    <Section title={REVIEW_COPY.drawer.synthesis}>
      {body}
      {message === null ? null : (
        <p role="alert" className="text-sm text-destructive">
          {message}
        </p>
      )}
    </Section>
  )
}
