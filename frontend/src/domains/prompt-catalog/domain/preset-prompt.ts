const PLACEHOLDER_PATTERN = /\{\{(\w+)\}\}/g

// Parity with the legacy client's `_getPlaceholders`: unique, first-occurrence order — repeated
// and adjacent placeholders collapse to one entry each.
export const extractRequiredVariables = (template: string): readonly string[] => {
  const found = new Set<string>()

  for (const match of template.matchAll(PLACEHOLDER_PATTERN)) {
    const name = match[1]
    if (name !== undefined) {
      found.add(name)
    }
  }

  return [...found]
}

// CONTRACT-001 §6: `presetPrompts[]` item shape, already camelCased by the bootstrap DTO.
export type PresetPromptWireConfig = {
  readonly id: string
  readonly label: string
  readonly description: string
  readonly template: string
  readonly group?: string | undefined
}

export type PresetPrompt = {
  readonly id: string
  readonly label: string
  readonly displayLabel: string
  readonly group: string | null
  readonly description: string
  readonly template: string
  readonly requiredVariables: readonly string[]
}

export const parsePresetPrompt = (config: PresetPromptWireConfig): PresetPrompt => {
  const group = config.group?.trim() || null
  const prefix = group === null ? null : `${group} - `

  return {
    id: config.id,
    label: config.label,
    displayLabel: prefix !== null && config.label.startsWith(prefix)
      ? config.label.slice(prefix.length)
      : config.label,
    group,
    description: config.description,
    template: config.template,
    requiredVariables: extractRequiredVariables(config.template),
  }
}
