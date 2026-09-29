import type { Logger } from '@/shared/logging/logger'
import { isOk } from '@/shared/kernel/result'

import { composePreset } from '@/domains/prompt-catalog/domain/composed-prompt'
import { parseDeepLink, scrubDeepLink, type DeepLinkIntent } from '@/domains/prompt-catalog/domain/deep-link'
import { detectBindings } from '@/domains/prompt-catalog/domain/detection'
import { evaluatePreset, type PresetEvaluation } from '@/domains/prompt-catalog/domain/enablement'
import type { PromptCatalog } from '@/domains/prompt-catalog/domain/prompt-catalog'
import { bindingFromText, defaultBindingFor } from '@/domains/prompt-catalog/domain/prompt-variable'
import type { VariableValue } from '@/domains/prompt-catalog/domain/variable-value'
import type { ComposedPromptSink, UrlNavigator } from '@/domains/prompt-catalog/application/ports'

export type PromptCatalogSnapshot = {
  readonly bindings: Readonly<Record<string, VariableValue>>
  /** FR-PROMPT-015: names currently showing the "detected" badge. */
  readonly detected: ReadonlySet<string>
}

export type ActivateOutcome =
  | { readonly kind: 'sent' }
  | { readonly kind: 'blocked'; readonly evaluation: PresetEvaluation }
  | { readonly kind: 'unknown-preset' }

export type PromptCatalogStoreOptions = {
  readonly catalog: PromptCatalog
  readonly sink: ComposedPromptSink
  readonly urlNavigator: UrlNavigator
  readonly logger: Logger
}

// SPEC-013 §4. Owns variable bindings, which of them the user has edited explicitly (so
// auto-detection never overrides a real selection) vs. auto-detected (FR-PROMPT-015), and the
// deep-link lifecycle, which must resolve to *at most one* auto-send no matter how many times it
// is retried (FR-PROMPT-016). `evaluatePreset`/`composePreset` stay pure and are called fresh off
// the current snapshot rather than cached.
export class PromptCatalogStore {
  readonly catalog: PromptCatalog
  readonly #sink: ComposedPromptSink
  readonly #urlNavigator: UrlNavigator
  readonly #logger: Logger
  readonly #listeners = new Set<() => void>()
  #bindings: Record<string, VariableValue>
  #edited = new Set<string>()
  #detected = new Set<string>()
  #snapshot: PromptCatalogSnapshot
  // Three independent monotonic flags: a link can be *parsed* (once, ever) with `autosend=0` (no
  // send ever due), *prefilled* (once, ever) regardless of readiness, and *sent* (at most once,
  // ever) — parsing/prefilling happen the first time this runs at all; sending waits for readiness
  // and may take several calls (re-render, reconnect) to become true.
  #deepLinkIntent: DeepLinkIntent | null | undefined
  #deepLinkPrefilled = false
  #deepLinkSent = false

  constructor(options: PromptCatalogStoreOptions) {
    this.catalog = options.catalog
    this.#sink = options.sink
    this.#urlNavigator = options.urlNavigator
    this.#logger = options.logger
    this.#bindings = Object.fromEntries(
      Object.values(options.catalog.variables).map((variable) => [variable.name, defaultBindingFor(variable)]),
    )
    this.#snapshot = this.#buildSnapshot()
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  getSnapshot = (): PromptCatalogSnapshot => this.#snapshot

  // FR-PROMPT-013: always domain state, never the DOM — VariableField is a pure projection of it.
  bindVariable(name: string, value: VariableValue): void {
    if (this.catalog.variables[name] === undefined) {
      return
    }

    this.#bindings = { ...this.#bindings, [name]: value }
    this.#edited.add(name)
    this.#detected.delete(name)
    this.#emit()
  }

  // FR-PROMPT-007/007a: run against every manually-sent message, before it goes out.
  detectFromMessage(text: string): void {
    const found = detectBindings(text, this.catalog.variables, this.#edited)
    const names = Object.keys(found)
    if (names.length === 0) {
      return
    }

    this.#bindings = { ...this.#bindings, ...found }
    for (const name of names) {
      this.#detected.add(name)
    }
    this.#emit()
  }

  // FR-PROMPT-005/006/014: blocked without composing/submitting unless every required variable
  // validates.
  activatePreset(presetId: string): ActivateOutcome {
    const preset = this.catalog.presets.find((candidate) => candidate.id === presetId)
    if (preset === undefined) {
      return { kind: 'unknown-preset' }
    }

    const evaluation = evaluatePreset(preset, this.catalog.variables, this.#bindings)
    if (!evaluation.enabled) {
      return { kind: 'blocked', evaluation }
    }

    const composed = composePreset(preset, this.#bindings)
    if (!isOk(composed)) {
      // Defensive: `evaluation.enabled` already guarantees every required binding is present.
      return { kind: 'blocked', evaluation }
    }

    this.#sink.submit(composed.value)
    return { kind: 'sent' }
  }

  // FR-PROMPT-008*/009/016. Idempotent: call it as often as readiness changes (mount, reconnect,
  // re-render) — parsing, prefilling and sending each happen at most once regardless.
  tryAutoSend(ready: boolean): void {
    const intent = this.#resolveIntent()
    if (intent === null) {
      return
    }

    if (!this.#deepLinkPrefilled) {
      this.#deepLinkPrefilled = true
      this.#applyPrefill(intent)
    }

    if (this.#deepLinkSent || !intent.autoSend || !ready) {
      return
    }

    this.#send(intent)
  }

  #resolveIntent(): DeepLinkIntent | null {
    if (this.#deepLinkIntent === undefined) {
      const url = this.#urlNavigator.current()
      this.#deepLinkIntent = parseDeepLink(url, this.catalog)

      if (this.#deepLinkIntent === null && url.prompt !== undefined) {
        this.#logger.warn('prompt_deep_link_unknown_preset', { presetId: url.prompt })
      }
    }

    return this.#deepLinkIntent
  }

  #applyPrefill(intent: DeepLinkIntent): void {
    const next = { ...this.#bindings }

    for (const [name, raw] of Object.entries(intent.bindings)) {
      const variable = this.catalog.variables[name]
      if (variable === undefined) {
        continue
      }

      next[name] = bindingFromText(variable, raw)
      this.#edited.add(name)
    }

    this.#bindings = next
    this.#emit()
  }

  #send(intent: DeepLinkIntent): void {
    const preset = this.catalog.presets.find((candidate) => candidate.id === intent.presetId)
    const evaluation = preset === undefined ? undefined : evaluatePreset(preset, this.catalog.variables, this.#bindings)

    if (preset === undefined || evaluation === undefined || !evaluation.enabled) {
      if (evaluation !== undefined) {
        this.#logger.warn('prompt_deep_link_not_ready', { missing: evaluation.missing, invalid: evaluation.invalid })
      }
      return
    }

    const composed = composePreset(preset, this.#bindings)
    if (!isOk(composed)) {
      return
    }

    // FR-PROMPT-016: flip before the side effects, so a reentrant call from within `sink.submit`
    // can never observe a not-yet-sent state.
    this.#deepLinkSent = true
    this.#sink.submit(composed.value)
    this.#urlNavigator.replace(scrubDeepLink(this.#urlNavigator.current(), intent))
  }

  #buildSnapshot(): PromptCatalogSnapshot {
    return { bindings: this.#bindings, detected: new Set(this.#detected) }
  }

  #emit(): void {
    this.#snapshot = this.#buildSnapshot()

    for (const listener of this.#listeners) {
      listener()
    }
  }
}
