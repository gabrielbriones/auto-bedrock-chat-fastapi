import { ArrowUpRight } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { PROMPT_CATALOG_COPY } from '@/shared/copy/prompt-catalog'

import { evaluatePreset, type PresetEvaluation } from '@/domains/prompt-catalog/domain/enablement'
import type { PromptCatalog } from '@/domains/prompt-catalog/domain/prompt-catalog'
import type { PresetPrompt } from '@/domains/prompt-catalog/domain/preset-prompt'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
import { PresetDisabledReason } from '@/domains/prompt-catalog/presentation/PresetDisabledReason'

export type PresetPromptBarProps = {
  readonly catalog: PromptCatalog
  readonly bindings: Readonly<Record<string, VariableValue>>
  /** e.g. unauthenticated/offline/awaiting-a-response — applies to every preset uniformly. */
  readonly locked: boolean
  readonly lockedReason: string
  readonly onActivate: (presetId: string) => void
}

type PresetButtonProps = Omit<PresetPromptBarProps, 'catalog'> & {
  readonly preset: PresetPrompt
  readonly variables: PromptCatalog['variables']
}

const disabledReason = (
  locked: boolean,
  lockedReason: string,
  evaluation: PresetEvaluation,
  variables: PromptCatalog['variables'],
): string => {
  if (locked) {
    return lockedReason
  }

  const labels = [...evaluation.missing, ...evaluation.invalid].map((name) => variables[name]?.label ?? name)
  return PROMPT_CATALOG_COPY.bar.disabledReason(labels)
}

function PresetButton({ preset, variables, bindings, locked, lockedReason, onActivate }: PresetButtonProps) {
  const evaluation = evaluatePreset(preset, variables, bindings)
  const disabled = locked || !evaluation.enabled
  const descriptionId = preset.description === '' ? undefined : `preset-${preset.id}-description`
  const reasonId = disabled ? `preset-${preset.id}-reason` : undefined
  const describedBy = [descriptionId, reasonId].filter((id) => id !== undefined).join(' ') || undefined
  const hint = disabled ? disabledReason(locked, lockedReason, evaluation, variables) : preset.description
  return (
    <div className="flex min-w-0 max-w-full flex-col gap-1">
      <Tooltip>
        <TooltipTrigger render={<span />} tabIndex={disabled ? 0 : -1} aria-label={preset.label} aria-describedby={describedBy}>
          <Button type="button" variant="outline" className="h-auto min-h-11 w-full justify-between gap-3 rounded-md px-3 py-2.5 text-left whitespace-normal disabled:opacity-70 [overflow-wrap:anywhere]" disabled={disabled} aria-describedby={describedBy} onClick={() => onActivate(preset.id)}>
            <span>{preset.displayLabel}</span>
            <ArrowUpRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          </Button>
        </TooltipTrigger>
        {hint === '' ? null : <TooltipContent role="tooltip">{hint}</TooltipContent>}
      </Tooltip>
      {descriptionId === undefined ? null : <span id={descriptionId} className="sr-only">{preset.description}</span>}
      {reasonId === undefined ? null : <PresetDisabledReason id={reasonId} message={hint} />}
    </div>
  )
}

const groupedPresets = (presets: readonly PresetPrompt[]) => {
  const groups = new Map<string, PresetPrompt[]>()
  for (const preset of presets) {
    const label = preset.group ?? PROMPT_CATALOG_COPY.bar.otherGroup
    const items = groups.get(label)
    if (items !== undefined) {
      items.push(preset)
    } else {
      groups.set(label, [preset])
    }
  }
  return [...groups].map(([label, items]) => ({ label, items }))
}

// Optional source metadata controls grouping; catalogs without it retain the original flat layout.
export function PresetPromptBar(props: PresetPromptBarProps) {
  const { catalog } = props
  if (catalog.presets.length === 0) return null

  if (catalog.presets.every((preset) => preset.group === null)) {
    return <div role="group" aria-label={PROMPT_CATALOG_COPY.bar.label} className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
      {catalog.presets.map((preset) => <PresetButton key={preset.id} {...props} preset={preset} variables={catalog.variables} />)}
    </div>
  }

  return <div role="group" aria-label={PROMPT_CATALOG_COPY.bar.label} className="grid min-w-0 grid-cols-1 gap-5 sm:grid-cols-3">
    {groupedPresets(catalog.presets).map((group) => <section key={group.label} className="min-w-0">
      <h3 className="mb-2 text-sm font-medium text-foreground">{group.label}</h3>
      <div className="grid gap-2">
        {group.items.map((preset) => <PresetButton key={preset.id} {...props} preset={preset} variables={catalog.variables} />)}
      </div>
    </section>)}
  </div>
}
