import { TriangleAlertIcon } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { SKILLS_COPY } from '@/shared/copy/skills'

import type { Skill } from '@/domains/skills/domain/public'

const COPY = SKILLS_COPY.conflict

export type ConflictNoticeProps = {
  readonly current: Skill
  readonly disabled: boolean
  readonly onDiscard: () => void
}

// The server version is shown as plain text next to the admin's own draft, which stays in the form.
export function ConflictNotice({ current, disabled, onDiscard }: ConflictNoticeProps) {
  return (
    <Alert variant="destructive">
      <TriangleAlertIcon aria-hidden />
      <AlertTitle>{COPY.title(current.revision)}</AlertTitle>
      <AlertDescription className="grid gap-3">
        <p>{COPY.description}</p>
        <details className="rounded-md border border-border bg-background p-2 text-foreground">
          <summary className="cursor-pointer text-sm font-medium">{COPY.theirs}</summary>
          <dl className="mt-2 grid gap-2 text-sm">
            <dt className="font-medium">{SKILLS_COPY.editor.displayName}</dt>
            <dd>{current.displayName}</dd>
            <dt className="font-medium">{SKILLS_COPY.editor.description}</dt>
            <dd className="whitespace-pre-wrap">{current.description}</dd>
            <dt className="font-medium">{SKILLS_COPY.editor.instructions}</dt>
            <dd className="max-h-60 overflow-y-auto font-mono text-xs break-words whitespace-pre-wrap">{current.instructions}</dd>
            <dt className="font-medium">{SKILLS_COPY.parameters.title}</dt>
            <dd>{COPY.parameters(current.parameters.length)}</dd>
          </dl>
        </details>
        <Button type="button" variant="outline" size="sm" className="justify-self-start" disabled={disabled} onClick={onDiscard}>
          {COPY.discard}
        </Button>
      </AlertDescription>
    </Alert>
  )
}
