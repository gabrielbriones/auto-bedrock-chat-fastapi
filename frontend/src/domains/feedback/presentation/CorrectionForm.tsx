import { useEffect, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { FEEDBACK_COPY } from '@/shared/copy/feedback'

import type { FeedbackDraft } from '@/domains/feedback/domain/public'

export type CorrectionFormProps = {
  readonly id: string
  readonly onCancel: () => void
  readonly onSubmit: (draft: FeedbackDraft) => void
}

export function CorrectionForm({ id, onCancel, onSubmit }: CorrectionFormProps) {
  const correctionId = useId()
  const commentId = useId()
  const correctionField = useRef<HTMLTextAreaElement>(null)
  const [correctionText, setCorrectionText] = useState('')
  const [userComment, setUserComment] = useState('')

  useEffect(() => {
    correctionField.current?.focus()
  }, [])

  return (
    <form
      id={id}
      className="grid gap-3"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit({ correctionText, userComment })
      }}
    >
      <div className="grid gap-2">
        <label htmlFor={correctionId}>{FEEDBACK_COPY.correction}</label>
        <Textarea
          ref={correctionField}
          id={correctionId}
          rows={3}
          value={correctionText}
          className="border-muted-foreground/50 bg-background/40"
          onChange={(event) => setCorrectionText(event.target.value)}
        />
      </div>
      <div className="grid gap-2">
        <label htmlFor={commentId}>{FEEDBACK_COPY.comment}</label>
        <Textarea
          id={commentId}
          rows={2}
          value={userComment}
          className="border-muted-foreground/50 bg-background/40"
          onChange={(event) => setUserComment(event.target.value)}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>
          {FEEDBACK_COPY.cancel}
        </Button>
        <Button type="submit">{FEEDBACK_COPY.submit}</Button>
      </div>
    </form>
  )
}