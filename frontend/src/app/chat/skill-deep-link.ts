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
  readonly send: (text: string) => void
  /** The preset deep link, for a `?prompt=` no skill answers to (FR-PROMPT-008). */
  readonly legacy: { tryAutoSend(ready: boolean): void }
  readonly logger: Logger
}

export type SkillDeepLinkDraft = { readonly text: string }

// `?skill=<name>&param=value` (and a legacy `?prompt=<id>` while skills replace presets) runs as
// `/<name> param=value`: sent once the composer is ready, or put in it with `autosend=0|false`.
// Acted on at most once; the keys it used are then scrubbed from the address bar.
export class SkillDeepLink {
  readonly #urlNavigator: UrlNavigator
  readonly #send: (text: string) => void
  readonly #legacy: SkillDeepLinkOptions['legacy']
  readonly #logger: Logger
  readonly #listeners = new Set<() => void>()
  #done = false
  #draft: SkillDeepLinkDraft | null = null

  constructor(options: SkillDeepLinkOptions) {
    this.#urlNavigator = options.urlNavigator
    this.#send = options.send
    this.#legacy = options.legacy
    this.#logger = options.logger
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)

    return () => {
      this.#listeners.delete(listener)
    }
  }

  /** The command an `autosend=0` link put in the composer, if any. */
  getSnapshot = (): SkillDeepLinkDraft | null => this.#draft

  /** Call whenever the catalog or readiness changes; waits for a non-empty catalog. */
  resolve(options: readonly SlashCommandOption[], ready: boolean): void {
    if (this.#done || options.length === 0) {
      return
    }

    const url = this.#urlNavigator.current()
    const targets = options.map((option) => ({ name: option.name, parameters: option.parameters.map((p) => p.name) }))
    const intent = parseSkillDeepLink(url, targets)
    if (intent === null) {
      if (url.skill !== undefined) {
        this.#done = true
        this.#logger.warn('skill_deep_link_unknown_skill', { skill: url.skill })
      }
      this.#legacy.tryAutoSend(ready)
      return
    }
    if (intent.autoSend && !ready) {
      return
    }

    const option = options.find((candidate) => candidate.name === intent.skill)
    if (option === undefined) {
      return
    }
    const command = buildSlashCommand(option, intent.values, intent.values[FREE_TEXT_PARAMETER] ?? '')

    this.#done = true
    this.#urlNavigator.replace(scrubSkillDeepLink(url, intent))
    if (intent.autoSend) {
      this.#send(command)
      return
    }
    this.#draft = { text: command }
    for (const listener of this.#listeners) {
      listener()
    }
  }
}
