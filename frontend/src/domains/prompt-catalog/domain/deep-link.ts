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
