import { useState } from 'react'
import { Loader2Icon } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { MODEL_CONFIG_COPY } from '@/shared/copy/model-config'

import type { ModelCatalog } from '@/domains/model-config/domain/model-catalog'
import type { OverrideFieldDef } from '@/domains/model-config/domain/override-field'
import type { OverrideKey, OverrideValue } from '@/domains/model-config/domain/override-key'
import { ModelPicker } from '@/domains/model-config/presentation/ModelPicker'

export type OverrideFieldProps = {
  readonly field: OverrideFieldDef
  readonly value: OverrideValue | undefined
  readonly overridden: boolean
  readonly pending?: boolean
  readonly catalog: ModelCatalog
  readonly onCommit: (key: OverrideKey, value: OverrideValue) => void
}

const FIELD_HELP: Readonly<Partial<Record<OverrideKey, string>>> = MODEL_CONFIG_COPY.help

const helpTextFor = (key: OverrideKey): string | null => FIELD_HELP[key] ?? null

// FR-CFG-006a: an overridden field is visually marked, separate from its label text.
function FieldLabel({ htmlFor, field, overridden, pending }: { readonly htmlFor: string; readonly field: OverrideFieldDef; readonly overridden: boolean; readonly pending: boolean }) {
  const help = field.hasHelp ? helpTextFor(field.key) : null

  return (
    <div className="flex items-center gap-1.5">
      <Label htmlFor={htmlFor}>{MODEL_CONFIG_COPY.fields[field.key]}</Label>
      {overridden ? <span aria-hidden="true" data-slot="override-marker" className="size-1.5 rounded-full bg-primary" /> : null}
      {pending ? (
        <span role="status" className="inline-flex text-muted-foreground">
          <Loader2Icon aria-hidden className="size-3.5 animate-spin" />
          <span className="sr-only">{MODEL_CONFIG_COPY.pending}</span>
        </span>
      ) : null}
      {help === null ? null : (
        <Tooltip>
          {/* FR-CFG-011: a focusable trigger, not a bare `title` attribute — reachable by keyboard. */}
          <TooltipTrigger
            render={<button type="button" className="text-xs text-muted-foreground underline decoration-dotted" />}
          >
            ?
          </TooltipTrigger>
          <TooltipContent>{help}</TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}

function SliderField({ field, value, overridden, pending = false, onCommit }: Omit<OverrideFieldProps, 'catalog'>) {
  const id = `override-${field.key}`
  const confirmed = typeof value === 'number' ? value : (field.min ?? 0)
  const [prevConfirmed, setPrevConfirmed] = useState(confirmed)
  const [draft, setDraft] = useState(confirmed)

  // Resyncs the displayed value whenever the confirmed value changes from outside (e.g. a server
  // `config_updated` frame, applied in Phase 4) — never while the user is actively dragging. Done
  // during render, not an effect, so an external change is reflected in the same commit.
  if (confirmed !== prevConfirmed) {
    setPrevConfirmed(confirmed)
    setDraft(confirmed)
  }

  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between">
        <FieldLabel htmlFor={id} field={field} overridden={overridden} pending={pending} />
        <span className="text-sm tabular-nums text-muted-foreground">{draft}</span>
      </div>
      <Slider
        id={id}
        getAriaLabel={() => MODEL_CONFIG_COPY.fields[field.key]}
        value={[draft]}
        min={field.min}
        max={field.max}
        step={field.step}
        disabled={pending}
        // FR-CFG-012: the thumb tracks every pointer move, but only release commits.
        onValueChange={(next) => setDraft(Array.isArray(next) ? next[0] : next)}
        onValueCommitted={(next) => onCommit(field.key, Array.isArray(next) ? next[0] : next)}
      />
    </div>
  )
}

function NumberField({ field, value, overridden, pending = false, onCommit }: Omit<OverrideFieldProps, 'catalog'>) {
  const id = `override-${field.key}`
  const confirmed = typeof value === 'number' ? value : (field.min ?? 0)
  const [prevConfirmed, setPrevConfirmed] = useState(confirmed)
  const [draft, setDraft] = useState(String(confirmed))

  if (confirmed !== prevConfirmed) {
    setPrevConfirmed(confirmed)
    setDraft(String(confirmed))
  }

  const commit = () => {
    const parsed = Number(draft)
    if (draft.trim() !== '' && Number.isFinite(parsed)) {
      onCommit(field.key, parsed)
    }
  }

  return (
    <div className="grid gap-2">
      <FieldLabel htmlFor={id} field={field} overridden={overridden} pending={pending} />
      <Input
        id={id}
        type="number"
        value={draft}
        min={field.min}
        max={field.max}
        step={field.step}
        disabled={pending}
        onChange={(event) => setDraft(event.target.value)}
        // FR-CFG-012: committed on blur, never on every keystroke.
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.currentTarget.blur()
          }
        }}
      />
    </div>
  )
}

function SwitchField({ field, value, overridden, pending = false, onCommit }: Omit<OverrideFieldProps, 'catalog'>) {
  const id = `override-${field.key}`

  return (
    <div className="flex items-center justify-between gap-2">
      <FieldLabel htmlFor={id} field={field} overridden={overridden} pending={pending} />
      {/* A toggle's own change already is the commit — there is no intermediate state to buffer. */}
      <Switch id={id} checked={value === true} disabled={pending} onCheckedChange={(next) => onCommit(field.key, next)} />
    </div>
  )
}

function ModelPickerField({ field, value, overridden, pending = false, catalog, onCommit }: OverrideFieldProps) {
  const id = `override-${field.key}`

  return (
    <div className="grid gap-2">
      <FieldLabel htmlFor={id} field={field} overridden={overridden} pending={pending} />
      <ModelPicker
        catalog={catalog}
        selectedModelId={typeof value === 'string' ? value : null}
        disabled={pending}
        onSelect={(modelId) => onCommit('model_id', modelId)}
      />
    </div>
  )
}

// SPEC-014 §5: dispatches on `field.control`. Every variant commits explicitly (release / blur /
// selection), never on an intermediate keystroke or drag frame (ADR-009, FR-CFG-012).
export function OverrideField(props: OverrideFieldProps) {
  switch (props.field.control) {
    case 'model-picker':
      return <ModelPickerField {...props} />
    case 'slider':
      return <SliderField {...props} />
    case 'number':
      return <NumberField {...props} />
    case 'switch':
      return <SwitchField {...props} />
  }
}
