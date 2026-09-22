import { useRef, useState, type RefObject } from 'react'
import { PlusIcon, XIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { REVIEW_COPY } from '@/shared/copy/review'
import { addTag, MAX_TAG_LENGTH, type TagAddError } from '@/shared/kernel/curation'
import { isErr } from '@/shared/kernel/result'

export type TagChipInputProps = {
  readonly id: string
  readonly label: string
  readonly tags: readonly string[]
  readonly onChange: (tags: readonly string[]) => void
  readonly error?: string | null
  readonly disabled?: boolean
}

type ChipListProps = Pick<TagChipInputProps, 'tags' | 'onChange' | 'disabled'> & {
  readonly onRemove: () => void
}

function ChipList({ tags, onChange, disabled = false, onRemove }: ChipListProps) {
  return tags.map((tag) => (
    <Badge key={tag} variant="secondary" className="gap-1 pr-1">
      {tag}
      <button
        type="button"
        aria-label={REVIEW_COPY.tags.remove(tag)}
        disabled={disabled}
        className="rounded-sm p-0.5 focus-visible:outline-2 focus-visible:outline-ring"
        onClick={() => {
          onChange(tags.filter((candidate) => candidate !== tag))
          onRemove()
        }}
      >
        <XIcon aria-hidden className="size-3" />
      </button>
    </Badge>
  ))
}

type TagDraftProps = {
  readonly id: string
  readonly draft: string
  readonly tags: readonly string[]
  readonly message: string | null
  readonly disabled: boolean
  readonly inputRef: RefObject<HTMLInputElement | null>
  readonly onDraftChange: (value: string) => void
  readonly onChange: (tags: readonly string[]) => void
  readonly onCommit: () => void
}

function TagDraft({ id, draft, tags, message, disabled, inputRef, onDraftChange, onChange, onCommit }: TagDraftProps) {
  return <>
    <Input
      ref={inputRef}
      id={id}
      value={draft}
      disabled={disabled}
      aria-invalid={message === null ? undefined : true}
      aria-describedby={message === null ? undefined : `${id}-error`}
      className="h-7 min-w-32 flex-1 border-0 p-0 shadow-none focus-visible:ring-0"
      placeholder={REVIEW_COPY.tags.input}
      onChange={(event) => onDraftChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ',') {
          event.preventDefault()
          onCommit()
        } else if (event.key === 'Backspace' && draft.length === 0 && tags.length > 0) {
          onChange(tags.slice(0, -1))
        }
      }}
    />
    <Button type="button" variant="ghost" size="icon-sm" aria-label={REVIEW_COPY.tags.add} disabled={disabled} onClick={onCommit}>
      <PlusIcon aria-hidden />
    </Button>
  </>
}

const TagError = ({ id, message }: { readonly id: string; readonly message: string | null }) =>
  message === null ? null : <p id={`${id}-error`} role="alert" className="text-sm text-destructive">{message}</p>

const messageFor = (error: TagAddError): string => {
  switch (error.kind) {
    case 'empty-tag':
      return REVIEW_COPY.tags.empty
    case 'too-many-tags':
      return REVIEW_COPY.tags.maximum
    case 'duplicate-tag':
      return REVIEW_COPY.tags.duplicate
    case 'invalid-tag':
      return error.tag.length > MAX_TAG_LENGTH ? REVIEW_COPY.tags.tooLong : REVIEW_COPY.tags.pattern
  }
}

export function TagChipInput({
  id,
  label,
  tags,
  onChange,
  error = null,
  disabled = false,
}: TagChipInputProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState('')
  const [policyError, setPolicyError] = useState<string | null>(null)
  const message = error ?? policyError

  const commit = () => {
    const result = addTag(tags, draft)

    if (isErr(result)) {
      setPolicyError(messageFor(result.error))
      return
    }

    onChange(result.value)
    setDraft('')
    setPolicyError(null)
  }

  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <div
        className="flex min-h-10 flex-wrap items-center gap-2 rounded-lg border border-input p-2 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
        onClick={() => inputRef.current?.focus()}
      >
        <ChipList tags={tags} onChange={onChange} disabled={disabled} onRemove={() => setPolicyError(null)} />
        <TagDraft
          id={id}
          draft={draft}
          tags={tags}
          message={message}
          disabled={disabled}
          inputRef={inputRef}
          onDraftChange={(value) => { setDraft(value); setPolicyError(null) }}
          onChange={onChange}
          onCommit={commit}
        />
      </div>
      <TagError id={id} message={message} />
    </div>
  )
}