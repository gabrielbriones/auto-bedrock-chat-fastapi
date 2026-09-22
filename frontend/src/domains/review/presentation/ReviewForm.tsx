import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { TagChipInput } from '@/components/ui/composed/tag-chip-input'
import { REVIEW_COPY } from '@/shared/copy/review'
import type { Problem } from '@/shared/http/exception'
import { isErr } from '@/shared/kernel/result'

import {
  createReviewDecisionDraft,
  initialDecision,
  type FeedbackEntry,
  type ReviewDecision,
  type ReviewDecisionDraft,
} from '@/domains/review/domain/public'

export type ReviewFormProps = {
  readonly entry: FeedbackEntry
  readonly pending: boolean
  readonly problem: Problem | null
  readonly onSave: (draft: ReviewDecisionDraft) => void
}

function DecisionField({ decision, message, pending, onChange }: {
  readonly decision: ReviewDecision | null
  readonly message: string | null
  readonly pending: boolean
  readonly onChange: (decision: ReviewDecision) => void
}) {
  const errorId = 'review-decision-error'
  return <fieldset className="grid gap-2" aria-invalid={message === null ? undefined : true} aria-describedby={message === null ? undefined : errorId}>
    <legend className="text-sm font-medium">{REVIEW_COPY.form.decision}</legend>
    <div className="flex gap-4">
      {(['approved', 'rejected'] as const).map((value) => (
        <Label key={value} className="flex items-center gap-2">
          <input type="radio" name="review-decision" value={value} checked={decision === value} disabled={pending} onChange={() => onChange(value)} />
          {value === 'approved' ? REVIEW_COPY.filters.approved : REVIEW_COPY.filters.rejected}
        </Label>
      ))}
    </div>
    {message === null ? null : <p id={errorId} role="alert" className="text-sm text-destructive">{message}</p>}
  </fieldset>
}

function CommentField({ value, error, pending, onChange }: {
  readonly value: string
  readonly error: string | null
  readonly pending: boolean
  readonly onChange: (value: string) => void
}) {
  const errorId = 'reviewer-comment-error'
  return <div className="grid gap-2">
    <Label htmlFor="reviewer-comment">{REVIEW_COPY.form.comment}</Label>
    <Textarea id="reviewer-comment" value={value} disabled={pending} aria-invalid={error === null ? undefined : true} aria-describedby={error === null ? undefined : errorId} onChange={(event) => onChange(event.target.value)} />
    {error === null ? null : <p id={errorId} role="alert" className="text-sm text-destructive">{error}</p>}
  </div>
}

const problemMessage = (problem: Problem | null): string | null => {
  if (problem === null) {
    return null
  }

  if (problem.status === 409) {
    return problem.detail ?? REVIEW_COPY.form.conflict
  }

  if (problem.status === 422) {
    return REVIEW_COPY.form.validation(problem.detail ?? problem.title)
  }

  return REVIEW_COPY.form.failure
}

type ServerField = 'decision' | 'tags' | 'comment' | null

const problemField = (problem: Problem | null): ServerField => {
  if (problem?.status !== 422 || problem.detail === undefined) {
    return null
  }

  const detail = problem.detail.toLowerCase()
  if (detail.includes('tag')) return 'tags'
  if (detail.includes('comment')) return 'comment'
  if (detail.includes('status') || detail.includes('decision')) return 'decision'
  return null
}

export function ReviewForm({ entry, pending, problem, onSave }: ReviewFormProps) {
  const [decision, setDecision] = useState<ReviewDecision | null>(() => initialDecision(entry))
  const [tags, setTags] = useState(entry.review?.tags ?? [])
  const [comment, setComment] = useState(entry.review?.comment ?? '')
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const serverError = problemMessage(problem)
  const serverField = problemField(problem)
  const decisionMessage = decisionError ?? (serverField === 'decision' ? serverError : null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const result = createReviewDecisionDraft({ decision, tags, comment })

    if (isErr(result)) {
      if (result.error.kind === 'decision-required') {
        setDecisionError(REVIEW_COPY.form.requiredDecision)
      }
      return
    }

    setDecisionError(null)
    onSave(result.value)
  }

  return (
    <form className="grid gap-5 border-t border-border pt-4" onSubmit={submit}>
      <DecisionField decision={decision} message={decisionMessage} pending={pending} onChange={(value) => { setDecision(value); setDecisionError(null) }} />

      <TagChipInput
        id="reviewer-tags"
        label={REVIEW_COPY.form.tags}
        tags={tags}
        onChange={setTags}
        error={serverField === 'tags' ? serverError : null}
        disabled={pending}
      />

      <CommentField value={comment} error={serverField === 'comment' ? serverError : null} pending={pending} onChange={setComment} />

      {serverError === null || serverField !== null ? null : (
        <p role="alert" className="text-sm text-destructive">
          {serverError}
        </p>
      )}

      <Button type="submit" disabled={pending} aria-label={pending ? REVIEW_COPY.form.saving : undefined}>
        {REVIEW_COPY.form.save}
      </Button>
    </form>
  )
}