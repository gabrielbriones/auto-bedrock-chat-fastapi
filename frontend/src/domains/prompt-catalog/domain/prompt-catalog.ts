import {
  parsePresetPrompt,
  type PresetPrompt,
  type PresetPromptWireConfig,
} from '@/domains/prompt-catalog/domain/preset-prompt'
import {
  inferPromptVariable,
  parsePromptVariable,
  type PromptVariable,
  type PromptVariableWireConfig,
} from '@/domains/prompt-catalog/domain/prompt-variable'

export type PromptCatalog = {
  readonly presets: readonly PresetPrompt[]
  readonly variables: Readonly<Record<string, PromptVariable>>
  /** Parse-time diagnostics (currently: uncompilable `validate` regexes) — never thrown. */
  readonly diagnostics: readonly string[]
}

// SPEC-013 §1 / DESIGN-001 §8: the one place that turns the bootstrap's `presetPrompts[]` +
// `variables[]` wire arrays into the prompt-catalog domain model.
export const parsePromptCatalog = (
  presetConfigs: readonly PresetPromptWireConfig[],
  variableConfigs: readonly PromptVariableWireConfig[],
): PromptCatalog => {
  const presets = presetConfigs.map(parsePresetPrompt)

  const variables: Record<string, PromptVariable> = {}
  const diagnostics: string[] = []

  for (const config of variableConfigs) {
    const parsed = parsePromptVariable(config)
    if (parsed === null) {
      continue
    }

    variables[parsed.variable.name] = parsed.variable
    diagnostics.push(...parsed.diagnostics)
  }

  // docs/preset-prompts.md "Variable Definitions": a placeholder with no `variables:` entry is
  // auto-inferred as a plain required text field.
  for (const preset of presets) {
    for (const name of preset.requiredVariables) {
      if (!(name in variables)) {
        variables[name] = inferPromptVariable(name)
      }
    }
  }

  return { presets, variables, diagnostics }
}

// The variable panel (Phase 2's `PresetPromptBar`) shows every variable any preset might need,
// not just the currently-hovered one — parity with the legacy client, which never scoped the
// panel to a single preset. Unique, first-occurrence order across `presets`.
export const allRequiredVariableNames = (catalog: PromptCatalog): readonly string[] => {
  const seen = new Set<string>()
  for (const preset of catalog.presets) {
    for (const name of preset.requiredVariables) {
      seen.add(name)
    }
  }
  return [...seen]
}
