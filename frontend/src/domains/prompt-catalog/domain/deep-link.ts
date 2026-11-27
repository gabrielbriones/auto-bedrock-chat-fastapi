import type { PromptCatalog } from '@/domains/prompt-catalog/domain/prompt-catalog'

export type UrlState = Readonly<Record<string, string>>

export type DeepLinkIntent = {
  readonly presetId: string
  /** Raw string values keyed by variable name — converted to `VariableValue` by the store. */
  readonly bindings: Readonly<Record<string, string>>
  readonly autoSend: boolean
}

const AUTOSEND_SUPPRESSED: ReadonlySet<string> = new Set(['0', 'false'])

// FR-PROMPT-008/008b: `?prompt=<id>` selects a preset; any other query key matching one of its
// required variables pre-fills that binding; `?autosend=0|false` suppresses the auto-send.
// Never throws; an absent `prompt` or an unknown preset id both parse to `null`.
export const parseDeepLink = (url: UrlState, catalog: PromptCatalog): DeepLinkIntent | null => {
  const presetId = url.prompt
  if (presetId === undefined) {
    return null
  }

  const preset = catalog.presets.find((candidate) => candidate.id === presetId)
  if (preset === undefined) {
    return null
  }

  const bindings: Record<string, string> = {}
  for (const name of preset.requiredVariables) {
    const value = url[name]
    if (value !== undefined) {
      bindings[name] = value
    }
  }

  const autosend = url.autosend
  const autoSend = autosend === undefined ? true : !AUTOSEND_SUPPRESSED.has(autosend)

  return { presetId, bindings, autoSend }
}

// FR-PROMPT-009: strips `prompt`, `autosend`, and every variable key the intent actually
// consumed — so a refresh or an SSO round trip can never see them again.
export const scrubDeepLink = (url: UrlState, intent: DeepLinkIntent): UrlState => {
  const consumed = new Set(['prompt', 'autosend', ...Object.keys(intent.bindings)])
  return Object.fromEntries(Object.entries(url).filter(([key]) => !consumed.has(key)))
}

/** A skill a link can run: its name and parameter names. */
export type SkillLinkTarget = {
  readonly name: string
  readonly parameters: readonly string[]
}

export type SkillDeepLinkIntent = {
  readonly skill: string
  /** Raw values keyed by parameter name. */
  readonly values: Readonly<Record<string, string>>
  readonly autoSend: boolean
  /** The query keys the link used, scrubbed once it has been acted on. */
  readonly consumed: readonly string[]
}

// `?skill=<name>&<param>=value` is the canonical form. A legacy `?prompt=<id>&VAR=value` names the
// default skill seeded from that preset: same name, `{{VAR}}` lowercased to parameter `var`.
// `null` without either key or for a skill not in `skills`.
export const parseSkillDeepLink = (url: UrlState, skills: readonly SkillLinkTarget[]): SkillDeepLinkIntent | null => {
  const key = url.skill === undefined ? 'prompt' : 'skill'
  const skill = skills.find((candidate) => candidate.name === url[key])
  if (skill === undefined) {
    return null
  }

  const values: Record<string, string> = {}
  const consumed = [key, 'autosend']
  for (const parameter of skill.parameters) {
    const source = [parameter, parameter.toUpperCase()].find((candidate) => url[candidate] !== undefined)
    if (source !== undefined) {
      values[parameter] = url[source] ?? ''
      consumed.push(source)
    }
  }

  const autosend = url.autosend
  return { skill: skill.name, values, autoSend: autosend === undefined || !AUTOSEND_SUPPRESSED.has(autosend), consumed }
}

export const scrubSkillDeepLink = (url: UrlState, intent: SkillDeepLinkIntent): UrlState =>
  Object.fromEntries(Object.entries(url).filter(([key]) => !intent.consumed.includes(key)))
