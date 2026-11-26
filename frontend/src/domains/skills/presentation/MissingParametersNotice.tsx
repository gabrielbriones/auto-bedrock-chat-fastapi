import { PlusIcon, TriangleAlertIcon } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { SKILLS_COPY } from '@/shared/copy/skills'

import { SKILL_NAME_PATTERN } from '@/domains/skills/domain/public'

const COPY = SKILLS_COPY.preview

export type MissingParametersNoticeProps = {
  readonly names: readonly string[]
  readonly disabled: boolean
  readonly onAdd: (name: string) => void
}

export function MissingParametersNotice({ names, disabled, onAdd }: MissingParametersNoticeProps) {
  if (names.length === 0) return null

  return (
    <Alert variant="destructive">
      <TriangleAlertIcon aria-hidden />
      <AlertTitle>{COPY.missingTitle}</AlertTitle>
      <AlertDescription className="grid gap-2">
        <p>{COPY.missingDescription}</p>
        <ul className="flex flex-wrap gap-2">
          {names.map((name) => (
            <li key={name}>
              {SKILL_NAME_PATTERN.test(name) ? (
                <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => onAdd(name)}>
                  <PlusIcon aria-hidden />
                  {COPY.addMissing(name)}
                </Button>
              ) : (
                <span className="text-sm">{COPY.invalidName(name)}</span>
              )}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  )
}
