import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import { isOk } from '@/shared/kernel/result'

import {
  EMPTY_FILE_INGEST_DRAFT,
  validateFileIngestDraft,
  type FileIngestDraft,
  type FileIngestIssues,
  type FileIngestRequest,
} from '@/domains/knowledge/domain/public'
import { DraftCheckbox, DraftTextField, FieldFrame } from '@/domains/knowledge/presentation/kb-source-fields'

const FILE = KNOWLEDGE_COPY.sources.file

export type FileIngestFormProps = {
  readonly busy: boolean
  readonly submitting: boolean
  readonly onSubmit: (request: FileIngestRequest) => Promise<boolean>
}

// Admins upload file content directly — there is no server-side path. The picker is keyed so a
// successful start clears the chosen files without touching the name and topic.
export function FileIngestForm({ busy, submitting, onSubmit }: FileIngestFormProps) {
  const [draft, setDraft] = useState<FileIngestDraft>(EMPTY_FILE_INGEST_DRAFT)
  const [issues, setIssues] = useState<FileIngestIssues>({})
  const [pickerKey, setPickerKey] = useState(0)
  const patch = (change: Partial<FileIngestDraft>) => setDraft((current) => ({ ...current, ...change }))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const validated = validateFileIngestDraft(draft)
    if (!isOk(validated)) {
      setIssues(validated.error)
      return
    }
    setIssues({})
    if (await onSubmit(validated.value)) {
      patch({ files: [] })
      setPickerKey((key) => key + 1)
    }
  }

  return (
    <form className="grid gap-5" aria-label={FILE.title} onSubmit={(event) => { void submit(event) }}>
      <DraftTextField label={FILE.name} value={draft.name} issue={issues.name} disabled={busy} onChange={(name) => patch({ name })} />
      <DraftTextField label={FILE.topic} value={draft.topic} disabled={busy} onChange={(topic) => patch({ topic })} />
      <FieldFrame label={FILE.files} hint={FILE.filesHint} issue={issues.files}>
        {({ id, describedBy, invalid }) => (
          <Input
            key={pickerKey}
            id={id}
            type="file"
            multiple
            disabled={busy}
            aria-invalid={invalid}
            aria-describedby={describedBy}
            onChange={(event) => patch({ files: Array.from(event.target.files ?? []) })}
          />
        )}
      </FieldFrame>
      <DraftCheckbox label={FILE.synthesize} hint={FILE.synthesizeHint} checked={draft.synthesize} disabled={busy} onChange={(synthesize) => patch({ synthesize })} />
      <Button type="submit" className="w-fit" disabled={busy} aria-label={submitting ? FILE.uploading : undefined}>
        {FILE.submit}
      </Button>
    </form>
  )
}
