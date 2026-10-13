import { ActivityIcon, ArrowUpRight, GitCompareArrowsIcon, LayersIcon, type LucideIcon } from 'lucide-react'

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
        <TooltipTrigger render={<span />} className={disabled ? 'cursor-not-allowed' : 'cursor-pointer'} tabIndex={disabled ? 0 : -1} aria-label={preset.label} aria-describedby={describedBy}>
          <Button type="button" variant="ghost" className="group/preset h-auto min-h-10 w-full justify-between gap-3 rounded-lg border border-transparent px-3 py-2 text-left text-sm font-normal whitespace-normal text-foreground/90 transition-all hover:border-primary/30 hover:bg-primary/10 hover:text-foreground disabled:opacity-50 [overflow-wrap:anywhere]" disabled={disabled} aria-describedby={describedBy} onClick={() => onActivate(preset.id)}>
            <span>{preset.displayLabel}</span>
            <ArrowUpRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover/preset:translate-x-0.5 group-hover/preset:-translate-y-0.5 group-hover/preset:text-primary" aria-hidden="true" />
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

// Group labels are catalog data, so icons are picked by keyword with a generic fallback.
const groupIcon = (label: string): LucideIcon => {
  const key = label.toLowerCase()
  if (key.includes('analy')) return ActivityIcon
  if (key.includes('valid') || key.includes('compar')) return GitCompareArrowsIcon
  return LayersIcon
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

  return <div role="group" aria-label={PROMPT_CATALOG_COPY.bar.label} className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-3">
    {groupedPresets(catalog.presets).map((group) => {
      const Icon = groupIcon(group.label)
      return <section key={group.label} className="flex min-w-0 flex-col rounded-xl border border-border/60 bg-card/40 p-3 shadow-sm backdrop-blur-sm">
      <div className="mb-2 flex items-center justify-between gap-2 border-b border-border/50 px-1 pb-2">
        <h3 className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">{group.label}</h3>
        <span aria-hidden="true" className="flex size-6 items-center justify-center rounded-full bg-primary/15 text-primary"><Icon className="size-3.5" /></span>
      </div>
      <div className="grid gap-1">
        {group.items.map((preset) => <PresetButton key={preset.id} {...props} preset={preset} variables={catalog.variables} />)}
      </div>
    </section>
    })}
  </div>
}
