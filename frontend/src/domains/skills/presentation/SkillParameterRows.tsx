import { ArrowDownIcon, ArrowUpIcon, PlusIcon, Trash2Icon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { SKILLS_COPY } from '@/shared/copy/skills'

import {
  charCount,
  PARAMETER_FIELD_MAX_CHARS,
  SKILL_INPUT_TYPES,
  type ParameterIssues,
  type SkillInputType,
  type SkillParameterDraft,
} from '@/domains/skills/domain/public'
import { FormField } from '@/domains/skills/presentation/FormField'
import { issueMessage } from '@/domains/skills/presentation/skill-messages'

const COPY = SKILLS_COPY.parameters
const SELECT_CLASS = 'h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm disabled:opacity-50'

type RowProps = {
  readonly parameter: SkillParameterDraft
  readonly issues: ParameterIssues
  readonly disabled: boolean
  readonly onChange: (change: Partial<SkillParameterDraft>) => void
}

type TextKey = 'label' | 'placeholder' | 'example'

function TextHint({ field, label, parameter, issues, disabled, onChange }: RowProps & { readonly field: TextKey; readonly label: string }) {
  return (
    <FormField label={label} error={issueMessage(issues[field])} counter={{ used: charCount(parameter[field]), max: PARAMETER_FIELD_MAX_CHARS[field] }}>
      {(control) => <Input {...control} value={parameter[field]} disabled={disabled} onChange={(event) => onChange({ [field]: event.target.value })} />}
    </FormField>
  )
}

function IdentityFields(props: RowProps) {
  const { parameter, issues, disabled, onChange } = props
  return (
    <>
      <FormField label={COPY.name} error={issueMessage(issues.name)}>
        {(control) => <Input {...control} className="font-mono" value={parameter.name} disabled={disabled} autoComplete="off" onChange={(event) => onChange({ name: event.target.value })} />}
      </FormField>
      <TextHint {...props} field="label" label={COPY.label} />
      <FormField label={COPY.inputType} error={null}>
        {(control) => (
          <select {...control} className={SELECT_CLASS} value={parameter.inputType} disabled={disabled} onChange={(event) => onChange({ inputType: event.target.value as SkillInputType })}>
            {SKILL_INPUT_TYPES.map((type) => <option key={type} value={type}>{COPY.inputTypes[type]}</option>)}
          </select>
        )}
      </FormField>
      <div className="flex items-center gap-2 self-end pb-2">
        <Checkbox id={`skill-param-${parameter.key}-required`} checked={parameter.required} disabled={disabled} onCheckedChange={(checked) => onChange({ required: checked === true })} />
        <Label htmlFor={`skill-param-${parameter.key}-required`}>{COPY.required}</Label>
      </div>
    </>
  )
}

function ValueFields(props: RowProps) {
  const { parameter, issues, disabled, onChange } = props
  const showOptions = parameter.inputType === 'select' || parameter.options.length > 0
  // An emptied default goes back to "no default" rather than an empty-string one.
  const setDefault = (value: string) => onChange({ default: value === '' ? null : value })
  return (
    <>
      <FormField label={COPY.default} error={issueMessage(issues.default)} counter={{ used: charCount(parameter.default ?? ''), max: PARAMETER_FIELD_MAX_CHARS.default }}>
        {(control) => <Input {...control} value={parameter.default ?? ''} disabled={disabled} onChange={(event) => setDefault(event.target.value)} />}
      </FormField>
      <TextHint {...props} field="placeholder" label={COPY.placeholder} />
      <TextHint {...props} field="example" label={COPY.example} />
      <FormField label={COPY.help} error={issueMessage(issues.help)} counter={{ used: charCount(parameter.help), max: PARAMETER_FIELD_MAX_CHARS.help }}>
        {(control) => <Textarea {...control} className="min-h-16" value={parameter.help} disabled={disabled} onChange={(event) => onChange({ help: event.target.value })} />}
      </FormField>
      {showOptions ? (
        <div className="sm:col-span-2">
          <FormField label={COPY.options} error={issueMessage(issues.options)}>
            {(control) => <Textarea {...control} className="min-h-20 font-mono" value={parameter.options.join('\n')} disabled={disabled} onChange={(event) => onChange({ options: event.target.value.split('\n') })} />}
          </FormField>
        </div>
      ) : null}
    </>
  )
}

type RowActionsProps = {
  readonly index: number
  readonly count: number
  readonly disabled: boolean
  readonly onMove: (offset: -1 | 1) => void
  readonly onRemove: () => void
}

function RowActions({ index, count, disabled, onMove, onRemove }: RowActionsProps) {
  const position = index + 1
  return (
    <div className="flex gap-1">
      <Button type="button" variant="ghost" size="icon-sm" aria-label={COPY.moveUp(position)} disabled={disabled || index === 0} onClick={() => onMove(-1)}>
        <ArrowUpIcon aria-hidden />
      </Button>
      <Button type="button" variant="ghost" size="icon-sm" aria-label={COPY.moveDown(position)} disabled={disabled || index === count - 1} onClick={() => onMove(1)}>
        <ArrowDownIcon aria-hidden />
      </Button>
      <Button type="button" variant="ghost" size="icon-sm" aria-label={COPY.remove(position)} disabled={disabled} onClick={onRemove}>
        <Trash2Icon aria-hidden />
      </Button>
    </div>
  )
}

export type SkillParameterRowsProps = {
  readonly parameters: readonly SkillParameterDraft[]
  readonly issues: readonly ParameterIssues[]
  readonly listError: string | null
  readonly disabled: boolean
  readonly onChange: (index: number, change: Partial<SkillParameterDraft>) => void
  readonly onMove: (index: number, offset: -1 | 1) => void
  readonly onRemove: (index: number) => void
  readonly onAdd: () => void
}

export function SkillParameterRows({ parameters, issues, listError, disabled, onChange, onMove, onRemove, onAdd }: SkillParameterRowsProps) {
  return (
    <section className="grid gap-3" aria-labelledby="skill-parameters-title">
      <h3 id="skill-parameters-title" className="text-sm font-semibold">{COPY.title}</h3>
      {parameters.length === 0 ? <p className="text-sm text-muted-foreground">{COPY.empty}</p> : null}
      {parameters.map((parameter, index) => (
        <fieldset key={parameter.key} className="rounded-lg border border-border p-3">
          <legend className="sr-only">{COPY.row(index + 1)}</legend>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <p aria-hidden className="text-sm font-medium">{COPY.row(index + 1)}</p>
              {parameter.autoAdded ? <Badge variant="secondary">{COPY.autoAdded}</Badge> : null}
            </div>
            <RowActions index={index} count={parameters.length} disabled={disabled} onMove={(offset) => onMove(index, offset)} onRemove={() => onRemove(index)} />
          </div>
          {parameter.autoAdded ? <p className="mt-1 text-xs text-muted-foreground">{COPY.autoAddedHint(parameter.name)}</p> : null}
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <IdentityFields parameter={parameter} issues={issues[index] ?? {}} disabled={disabled} onChange={(change) => onChange(index, change)} />
            <ValueFields parameter={parameter} issues={issues[index] ?? {}} disabled={disabled} onChange={(change) => onChange(index, change)} />
          </div>
        </fieldset>
      ))}
      {listError === null ? null : <p role="alert" className="text-sm text-destructive">{listError}</p>}
      <Button type="button" variant="outline" size="sm" className="justify-self-start" disabled={disabled} onClick={onAdd}>
        <PlusIcon aria-hidden />
        {COPY.add}
      </Button>
    </section>
  )
}
