import { useId, useImperativeHandle, useState, type FormEvent, type Ref } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { TagChipInput } from '@/components/ui/composed/tag-chip-input'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import type { Problem } from '@/shared/http/exception'
import { CalendarDate } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

import { diffDocument, type KbDocument, type KbDocumentDraft, type SparsePatch } from '@/domains/knowledge/domain/public'

export type KbEditorHandle = {
  readonly isDirty: () => boolean
}

export type KbEditorFormProps = {
  readonly document: KbDocument
  readonly pending: boolean
  readonly problem: Problem | null
  readonly onSave: (patch: SparsePatch) => void
  readonly ref?: Ref<KbEditorHandle>
}

type MetadataResult =
  | { readonly ok: true; readonly value: Record<string, unknown> }
  | { readonly ok: false }

const metadataText = (metadata: Readonly<Record<string, unknown>>): string => JSON.stringify(metadata, null, 2)

const parseMetadata = (text: string): MetadataResult => {
  try {
    const parsed: unknown = JSON.parse(text.trim().length === 0 ? '{}' : text)
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? { ok: true, value: parsed as Record<string, unknown> }
      : { ok: false }
  } catch {
    return { ok: false }
  }
}

const parseDate = (iso: string): CalendarDate | null => {
  if (iso === '') {
    return null
  }
  const result = CalendarDate.fromIso(iso)
  return isOk(result) ? result.value : null
}

const problemMessage = (problem: Problem | null): string | null => {
  if (problem === null) {
    return null
  }
  if (problem.status === 409) {
    return problem.detail ?? KNOWLEDGE_COPY.editor.conflict
  }
  if (problem.status === 422) {
    return KNOWLEDGE_COPY.editor.validation(problem.detail ?? problem.title)
  }
  return KNOWLEDGE_COPY.editor.saveFailure
}

type FieldState = {
  readonly title: string
  readonly topic: string
  readonly tags: readonly string[]
  readonly content: string
  readonly datePublished: string
  readonly metadataDraft: string
}

const initialState = (document: KbDocument): FieldState => ({
  title: document.title ?? '',
  topic: document.topic ?? '',
  tags: document.tags,
  content: document.content ?? '',
  datePublished: document.datePublished?.toIso() ?? '',
  metadataDraft: metadataText(document.metadata),
})

const draftFrom = (state: FieldState, metadata: Record<string, unknown>): KbDocumentDraft => ({
  title: state.title,
  topic: state.topic,
  tags: state.tags,
  content: state.content,
  datePublished: parseDate(state.datePublished),
  metadata,
})

function TitleTopicFields({ state, disabled, onChange }: {
  readonly state: FieldState
  readonly disabled: boolean
  readonly onChange: (patch: Partial<FieldState>) => void
}) {
  const titleId = useId()
  const topicId = useId()
  return (
    <>
      <div className="grid gap-2">
        <Label htmlFor={titleId}>{KNOWLEDGE_COPY.editor.title}</Label>
        <Input id={titleId} value={state.title} disabled={disabled} onChange={(event) => onChange({ title: event.target.value })} />
      </div>
      <div className="grid gap-2">
        <Label htmlFor={topicId}>{KNOWLEDGE_COPY.editor.topic}</Label>
        <Input id={topicId} value={state.topic} disabled={disabled} onChange={(event) => onChange({ topic: event.target.value })} />
      </div>
    </>
  )
}

function ContentDateFields({ state, disabled, onChange }: {
  readonly state: FieldState
  readonly disabled: boolean
  readonly onChange: (patch: Partial<FieldState>) => void
}) {
  const contentId = useId()
  const dateId = useId()
  return (
    <>
      <div className="grid gap-2">
        <Label htmlFor={dateId}>{KNOWLEDGE_COPY.editor.datePublished}</Label>
        <Input id={dateId} type="date" value={state.datePublished} disabled={disabled} onChange={(event) => onChange({ datePublished: event.target.value })} />
      </div>
      <div className="grid gap-2">
        <Label htmlFor={contentId}>{KNOWLEDGE_COPY.editor.content}</Label>
        <Textarea id={contentId} className="min-h-40" value={state.content} disabled={disabled} onChange={(event) => onChange({ content: event.target.value })} />
      </div>
    </>
  )
}

function MetadataField({ value, error, disabled, onChange }: {
  readonly value: string
  readonly error: string | null
  readonly disabled: boolean
  readonly onChange: (value: string) => void
}) {
  const metadataId = useId()
  return (
    <div className="grid gap-2">
      <Label htmlFor={metadataId}>{KNOWLEDGE_COPY.editor.metadata}</Label>
      <Textarea
        id={metadataId}
        className="min-h-24 font-mono text-sm"
        value={value}
        disabled={disabled}
        aria-invalid={error === null ? undefined : true}
        aria-describedby={error === null ? undefined : `${metadataId}-error`}
        onChange={(event) => onChange(event.target.value)}
      />
      {error === null ? null : <p id={`${metadataId}-error`} role="alert" className="text-sm text-destructive">{error}</p>}
    </div>
  )
}

export function KbEditorForm({ document, pending, problem, onSave, ref }: KbEditorFormProps) {
  const [state, setState] = useState<FieldState>(() => initialState(document))
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const tagsId = useId()
  const serverError = problemMessage(problem)

  const patch = (change: Partial<FieldState>) => setState((current) => ({ ...current, ...change }))

  const currentPatch = (): SparsePatch | null => {
    const parsed = parseMetadata(state.metadataDraft)
    if (!parsed.ok) {
      return null
    }
    return diffDocument(document, draftFrom(state, parsed.value))
  }

  useImperativeHandle(ref, () => ({
    isDirty: () => {
      const built = currentPatch()
      return built === null || Object.keys(built).length > 0
    },
  }))

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const built = currentPatch()
    if (built === null) {
      setMetadataError(KNOWLEDGE_COPY.editor.metadataInvalid)
      return
    }
    setMetadataError(null)
    onSave(built)
  }

  return (
    <form className="grid gap-5 border-t border-border pt-4" onSubmit={submit}>
      <TitleTopicFields state={state} disabled={pending} onChange={patch} />
      <TagChipInput
        id={tagsId}
        label={KNOWLEDGE_COPY.editor.tags}
        tags={state.tags}
        onChange={(tags) => patch({ tags })}
        disabled={pending}
      />
      <ContentDateFields state={state} disabled={pending} onChange={patch} />
      <MetadataField value={state.metadataDraft} error={metadataError} disabled={pending} onChange={(metadataDraft) => patch({ metadataDraft })} />

      {serverError === null ? null : (
        <p role="alert" className="text-sm text-destructive">{serverError}</p>
      )}

      <Button type="submit" disabled={pending} aria-label={pending ? KNOWLEDGE_COPY.editor.saving : undefined}>
        {KNOWLEDGE_COPY.editor.save}
      </Button>
    </form>
  )
}
