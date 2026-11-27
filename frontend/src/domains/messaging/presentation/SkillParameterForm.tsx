import { X } from 'lucide-react'
import { useEffect, useId, useRef, type KeyboardEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

import { parameterFields, type SlashCommandParameter } from '@/domains/messaging/domain/slash-command'
import type { SkillDraft } from '@/domains/messaging/presentation/useComposerDraft'

const { slashMenu: COPY } = MESSAGING_COPY.composer

const SELECT_CLASS =
  'h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30'

type FieldProps = {
  readonly parameter: SlashCommandParameter
  readonly value: string
  readonly disabled: boolean
  readonly onValue: (value: string) => void
  readonly onKeyDown: (event: KeyboardEvent<HTMLElement>) => void
  readonly inputRef?: (element: HTMLElement | null) => void
}

function ParameterControl({ parameter, value, disabled, onValue, onKeyDown, inputRef, id }: FieldProps & { readonly id: string }) {
  const shared = { id, disabled, onKeyDown, ref: inputRef, 'aria-required': parameter.required }

  if (parameter.inputType === 'checkbox') {
    return <input type="checkbox" {...shared} className="size-4" checked={value === 'true'} onChange={(event) => onValue(String(event.target.checked))} />
  }
  if (parameter.inputType === 'select' && parameter.options.length > 0) {
    return (
      <select {...shared} value={value} onChange={(event) => onValue(event.target.value)} className={SELECT_CLASS}>
        <option value="">{parameter.required ? COPY.choose : COPY.defaultOption}</option>
        {parameter.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  return (
    <Input
      {...shared}
      type={parameter.inputType === 'number' ? 'number' : 'text'}
      {...(parameter.inputType === 'number' ? { step: 'any' } : {})}
      value={value}
      placeholder={parameter.placeholder}
      onChange={(event) => onValue(event.target.value)}
    />
  )
}

function ParameterField(props: FieldProps) {
  const id = useId()
  const { parameter } = props

  return (
    <div className={parameter.inputType === 'checkbox' ? 'flex items-center gap-2' : 'flex min-w-0 flex-col gap-1'}>
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {parameter.label || parameter.name}
        {parameter.required && <span aria-hidden="true"> *</span>}
      </label>
      <ParameterControl {...props} id={id} />
    </div>
  )
}

export type SkillParameterFormProps = {
  readonly skill: SkillDraft
  readonly disabled: boolean
  readonly onValue: (name: string, value: string) => void
  readonly onCancel: () => void
}

// The chosen skill as a chip plus one field per parameter; Enter in a field sends (the form's
// submit), Escape drops the skill.
export function SkillParameterForm({ skill, disabled, onValue, onCancel }: SkillParameterFormProps) {
  const fields = parameterFields(skill.option)
  const first = useRef<HTMLElement | null>(null)
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onCancel()
    }
  }

  useEffect(() => {
    first.current?.focus()
  }, [skill.option.name])

  return (
    <div role="group" aria-label={COPY.parameters(skill.option.name)} className="flex flex-col gap-2 rounded-lg bg-muted/50 p-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className="rounded-md bg-primary/10 px-2 py-0.5 font-mono text-xs font-medium text-primary">/{skill.option.name}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{skill.option.description || skill.option.title}</span>
        <Button type="button" variant="ghost" size="icon-xs" aria-label={COPY.cancel} onClick={onCancel}>
          <X aria-hidden="true" />
        </Button>
      </div>
      {fields.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {fields.map((parameter, index) => (
            <ParameterField
              key={parameter.name}
              parameter={parameter}
              value={skill.values[parameter.name] ?? ''}
              disabled={disabled}
              onValue={(value) => onValue(parameter.name, value)}
              onKeyDown={onKeyDown}
              {...(index === 0 ? { inputRef: (element: HTMLElement | null) => { first.current = element } } : {})}
            />
          ))}
        </div>
      )}
    </div>
  )
}
