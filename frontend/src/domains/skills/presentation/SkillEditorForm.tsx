import { useImperativeHandle, useState, type FormEvent, type Ref } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import type { Problem } from '@/shared/http/exception'
import { SKILLS_COPY } from '@/shared/copy/skills'

import {
  charCount,
  contentFromDraft,
  DISPLAY_NAME_MAX_CHARS,
  hasIssues,
  unknownPlaceholders,
  validateSkillDraft,
  type Skill,
  type SkillCreateRequest,
  type SkillDraftIssues,
  type SkillLimits,
} from '@/domains/skills/domain/public'
import { ConflictNotice } from '@/domains/skills/presentation/ConflictNotice'
import { FormField } from '@/domains/skills/presentation/FormField'
import { InstructionsPreview } from '@/domains/skills/presentation/InstructionsPreview'
import { MissingParametersNotice } from '@/domains/skills/presentation/MissingParametersNotice'
import { SkillParameterRows } from '@/domains/skills/presentation/SkillParameterRows'
import { issueMessage, problemMessage } from '@/domains/skills/presentation/skill-messages'
import { useSkillDraft } from '@/domains/skills/presentation/use-skill-draft'

const COPY = SKILLS_COPY.editor

export type SkillEditorHandle = {
  readonly isDirty: () => boolean
}

export type SkillEditorFormProps = {
  /** `null` creates a new skill; otherwise the stored skill being edited. */
  readonly skill: Skill | null
  readonly limits: SkillLimits | null
  readonly saving: boolean
  readonly problem: Problem | null
  readonly conflict: Skill | null
  /** Carries `name` and `enabled` too; an edit only reads the content fields. */
  readonly onSubmit: (request: SkillCreateRequest) => void
  readonly onDiscardConflict?: () => void
  readonly ref?: Ref<SkillEditorHandle>
}

type EditorState = ReturnType<typeof useSkillDraft>

const NO_ISSUES: SkillDraftIssues = { fields: {}, parameters: [] }

function IdentityFields({ state, isNew, issues, disabled }: {
  readonly state: EditorState
  readonly isNew: boolean
  readonly issues: SkillDraftIssues
  readonly disabled: boolean
}) {
  const { draft, patch } = state
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <FormField label={COPY.name} {...(isNew ? { hint: COPY.nameHint } : {})} error={issueMessage(issues.fields.name)}>
        {(control) => <Input {...control} className="font-mono" value={draft.name} readOnly={!isNew} disabled={disabled} autoComplete="off" onChange={(event) => patch({ name: event.target.value })} />}
      </FormField>
      <FormField label={COPY.displayName} error={issueMessage(issues.fields.displayName)} counter={{ used: charCount(draft.displayName), max: DISPLAY_NAME_MAX_CHARS }}>
        {(control) => <Input {...control} value={draft.displayName} disabled={disabled} onChange={(event) => patch({ displayName: event.target.value })} />}
      </FormField>
    </div>
  )
}

function FormFooter({ state, isNew, saving, label }: {
  readonly state: EditorState
  readonly isNew: boolean
  readonly saving: boolean
  readonly label: string
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-4 border-t border-border pt-4">
      {isNew ? (
        <div className="flex items-center gap-2">
          <Switch id="skill-create-enabled" checked={state.enabled} disabled={saving} onCheckedChange={(checked) => state.setEnabled(checked)} />
          <Label htmlFor="skill-create-enabled">{COPY.enabled}</Label>
        </div>
      ) : null}
      <Button type="submit" disabled={saving}>{label}</Button>
    </div>
  )
}

function ContentFields({ state, limits, issues, disabled }: {
  readonly state: EditorState
  readonly limits: SkillLimits | null
  readonly issues: SkillDraftIssues
  readonly disabled: boolean
}) {
  const { draft, patch } = state
  const parameters = contentFromDraft(draft).parameters
  const counter = (used: number, max: number | undefined) => (max === undefined ? null : { used, max })
  return (
    <>
      <FormField label={COPY.description} error={issueMessage(issues.fields.description)} counter={counter(charCount(draft.description), limits?.descriptionMaxChars)}>
        {(control) => <Textarea {...control} className="min-h-16" value={draft.description} disabled={disabled} onChange={(event) => patch({ description: event.target.value })} />}
      </FormField>
      <div className="grid gap-4 lg:grid-cols-2">
        <FormField label={COPY.instructions} hint={COPY.instructionsHint} error={instructionsError(issues)} counter={counter(charCount(draft.instructions), limits?.instructionsMaxChars)}>
          {(control) => <Textarea {...control} className="min-h-64 font-mono text-sm" value={draft.instructions} disabled={disabled} onChange={(event) => state.setInstructions(event.target.value)} />}
        </FormField>
        <InstructionsPreview instructions={draft.instructions} parameters={parameters} />
      </div>
      <MissingParametersNotice names={unknownPlaceholders(draft.instructions, parameters)} disabled={disabled} onAdd={state.addParameter} />
    </>
  )
}

// Unmatched placeholders are reported by `MissingParametersNotice`, which stays visible while typing.
const instructionsError = (issues: SkillDraftIssues): string | null =>
  issues.fields.instructions?.kind === 'unknown-placeholders' ? null : issueMessage(issues.fields.instructions)

const submitLabel = (isNew: boolean, saving: boolean, conflict: Skill | null): string => {
  if (saving) return COPY.saving
  if (isNew) return COPY.create
  return conflict === null ? COPY.save : SKILLS_COPY.conflict.reapply(conflict.revision)
}

export function SkillEditorForm({ skill, limits, saving, problem, conflict, onSubmit, onDiscardConflict, ref }: SkillEditorFormProps) {
  const state = useSkillDraft(skill)
  const [submitted, setSubmitted] = useState(false)
  const isNew = skill === null
  const issues = submitted ? validateSkillDraft(state.draft, limits, isNew) : NO_ISSUES

  useImperativeHandle(ref, () => ({ isDirty: state.isDirty }))

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setSubmitted(true)
    if (hasIssues(validateSkillDraft(state.draft, limits, isNew))) return
    onSubmit({ ...contentFromDraft(state.draft), name: state.draft.name, enabled: state.enabled })
  }

  return (
    <form className="grid gap-5" aria-label={isNew ? COPY.createTitle : COPY.editTitle(state.draft.name)} noValidate onSubmit={submit}>
      {conflict === null || onDiscardConflict === undefined ? null : <ConflictNotice current={conflict} disabled={saving} onDiscard={onDiscardConflict} />}
      <IdentityFields state={state} isNew={isNew} issues={issues} disabled={saving} />
      <ContentFields state={state} limits={limits} issues={issues} disabled={saving} />
      <div className="border-t border-border pt-4">
        <SkillParameterRows
          parameters={state.draft.parameters}
          issues={issues.parameters}
          listError={issueMessage(issues.fields.parameters)}
          disabled={saving}
          onChange={state.changeParameter}
          onMove={state.moveParameter}
          onRemove={state.removeParameter}
          onAdd={() => state.addParameter()}
        />
      </div>
      {problem === null ? null : <p role="alert" className="text-sm text-destructive">{problemMessage(problem, isNew ? state.draft.name : undefined)}</p>}
      <FormFooter state={state} isNew={isNew} saving={saving} label={submitLabel(isNew, saving, conflict)} />
    </form>
  )
}
