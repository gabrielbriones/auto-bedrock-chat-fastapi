import type { Logger } from '@/shared/logging/logger'

import {
  buildSlashCommand,
  FREE_TEXT_PARAMETER,
  type SlashCommandOption,
} from '@/domains/messaging/domain/public'
import type { UrlNavigator } from '@/domains/prompt-catalog/application/ports'
import { parseSkillDeepLink, scrubSkillDeepLink } from '@/domains/prompt-catalog/domain/public'

export type SkillDeepLinkOptions = {
  readonly urlNavigator: UrlNavigator
  /** `refused` leaves the link to be tried again. */
  readonly send: (text: string) => 'sent' | 'empty' | 'refused' | 'dropped-closed'
  /** The preset deep link, for a `?prompt=` no skill answers to (FR-PROMPT-008). */
  readonly legacy: {
    tryAutoSend(ready: boolean): void
    readonly catalog: { readonly presets: readonly { readonly id: string }[] }
  }
  readonly logger: Logger
}

export type SkillDeepLinkSnapshot = {
  /** The command an `autosend=0` link put in the composer, until the composer acknowledges it. */
  readonly draft: { readonly text: string } | null
  /** A `?prompt=` link fell back to its preset, whose bar must then stay usable. */
  readonly presetFallback: boolean
}

// `?skill=<name>&param=value` (and a legacy `?prompt=<id>` while skills replace presets) runs as
// `/<name> param=value`: sent once the composer is ready, or put in it with `autosend=0|false`.
// Acted on at most once; the keys it used are then scrubbed from the address bar.
export class SkillDeepLink {
  readonly #options: SkillDeepLinkOptions
  readonly #listeners = new Set<() => void>()
  #done = false
  #snapshot: SkillDeepLinkSnapshot = { draft: null, presetFallback: false }

  constructor(options: SkillDeepLinkOptions) {
    this.#options = options
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  getSnapshot = (): SkillDeepLinkSnapshot => this.#snapshot

  acknowledgeDraft = (): void => {
    if (this.#snapshot.draft !== null) {
      this.#set({ ...this.#snapshot, draft: null })
    }
  }

  /** Call whenever the catalog or readiness changes; waits for a non-empty catalog. */
  resolve(options: readonly SlashCommandOption[], ready: boolean): void {
    if (this.#done || options.length === 0) {
      return
    }

    const url = this.#options.urlNavigator.current()
    const targets = options.map((option) => ({ name: option.name, parameters: option.parameters.map((p) => p.name) }))
    const intent = parseSkillDeepLink(url, targets)
    const option = options.find((candidate) => candidate.name === intent?.skill)
    if (intent === null || option === undefined) {
      this.#fallBack(url.skill, url.prompt, ready)
      return
    }
    if (intent.autoSend && !ready) {
      return
    }

    const command = buildSlashCommand(option, intent.values, intent.values[FREE_TEXT_PARAMETER] ?? '')
    if (intent.autoSend && this.#options.send(command) === 'refused') {
      return
    }
    this.#done = true
    this.#options.urlNavigator.replace(scrubSkillDeepLink(url, intent))
    if (!intent.autoSend) {
      this.#set({ ...this.#snapshot, draft: { text: command } })
    }
  }

  #fallBack(skill: string | undefined, prompt: string | undefined, ready: boolean): void {
    if (skill !== undefined) {
      this.#done = true
      this.#options.logger.warn('skill_deep_link_unknown_skill', { skill })
      return
    }
    const hasPreset = this.#options.legacy.catalog.presets.some((preset) => preset.id === prompt)
    if (hasPreset && !this.#snapshot.presetFallback) {
      this.#set({ ...this.#snapshot, presetFallback: true })
    }
    this.#options.legacy.tryAutoSend(ready)
  }

  #set(snapshot: SkillDeepLinkSnapshot): void {
    this.#snapshot = snapshot
    for (const listener of this.#listeners) {
      listener()
    }
  }
}
