import { useCallback, useEffect, useMemo } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import type { SkillDeepLinkSnapshot } from '@/app/chat/skill-deep-link'
import type { ConversationId } from '@/shared/kernel/branded'
import type { SlashCommandOption } from '@/domains/messaging/domain/public'
import type { ComposerCommands } from '@/domains/messaging/presentation/useSlashCommandMenu'
import type { SkillCatalogEntry, SkillParameter } from '@/domains/skills/domain/public'

// Where the `skills` catalog meets the `messaging` composer: neither context imports the other.

const placeholderOf = (parameter: SkillParameter): string => {
  if (parameter.inputType === 'select' && parameter.options.length > 0) {
    return parameter.options.join('|')
  }
  return `<${parameter.placeholder || parameter.label || parameter.name}>`
}

const usageOf = (entry: SkillCatalogEntry): string =>
  [
    `/${entry.name}`,
    ...entry.parameters.map((parameter) => {
      const pair = `${parameter.name}=${placeholderOf(parameter)}`
      return parameter.required ? pair : `[${pair}]`
    }),
  ].join(' ')

export const toSlashCommandOptions = (entries: readonly SkillCatalogEntry[]): readonly SlashCommandOption[] =>
  entries.map((entry) => ({
    name: entry.name,
    title: entry.displayName,
    description: entry.description,
    usage: usageOf(entry),
    parameters: entry.parameters.map((parameter) => ({
      name: parameter.name,
      label: parameter.label,
      required: parameter.required,
      inputType: parameter.inputType,
      options: parameter.options,
      placeholder: parameter.placeholder || parameter.example,
      defaultValue: parameter.default,
    })),
  }))

/** The enabled skills as composer commands, re-read when input opens and on composer focus once stale. */
export const useSkillCommands = (): ComposerCommands | undefined => {
  const { bootstrap, skillCatalog } = useContainer()
  const { inputEnabled } = useContainerStore('identity')
  const { entries, status } = useContainerStore('skillCatalog')
  const options = useMemo(() => toSlashCommandOptions(entries), [entries])
  const onFocus = useCallback(() => void skillCatalog.refresh(), [skillCatalog])
  const unavailable = status === 'failed' && entries.length === 0

  // Signing in can be what makes the catalog readable.
  useEffect(() => {
    if (bootstrap.skillsEnabled && inputEnabled) {
      onFocus()
    }
  }, [bootstrap.skillsEnabled, inputEnabled, onFocus])

  const commands = useMemo(() => ({ options, onFocus, unavailable }), [options, onFocus, unavailable])
  return bootstrap.skillsEnabled ? commands : undefined
}

/**
 * Skills replace the preset bar once the server has them and the catalog lists at least one. Until
 * the catalog has been read the presets stay hidden, so they do not flash; a failed or empty read
 * brings them back for the compatibility window.
 */
export const useSkillsActive = (): boolean => {
  const { bootstrap } = useContainer()
  const { entries, status } = useContainerStore('skillCatalog')
  return bootstrap.skillsEnabled && (entries.length > 0 || status === 'idle' || status === 'loading')
}

const NO_OPTIONS: readonly SlashCommandOption[] = []

export type SkillDeepLinkView = {
  readonly draftRequest: SkillDeepLinkSnapshot['draft']
  readonly acknowledgeDraft: () => void
  /** Presets show unless skills replace them, or a legacy link still needs its preset. */
  readonly showPresets: boolean
}

type RouteConversationState = {
  readonly visible: boolean
  readonly activeId: ConversationId | null
  readonly unknownId: ConversationId | null
  readonly pendingTurn: unknown
}

// The chat frame names no conversation, so a link may only run once the one its URL names is the one
// loaded (not a recovery state) and has no turn of its own running.
export const isOnRouteConversation = (state: RouteConversationState, routeId: ConversationId | null): boolean =>
  !state.visible || (state.activeId === routeId && state.unknownId === null && state.pendingTurn === null)

/** Runs a `?skill=` (or legacy `?prompt=`) link once the catalog is read and nothing is pending. */
export const useSkillDeepLink = (commands: ComposerCommands | undefined, routeId: ConversationId | null): SkillDeepLinkView => {
  const { skillDeepLink } = useContainer()
  const { inputEnabled } = useContainerStore('identity')
  const { connection, awaitingResponse } = useContainerStore('chatSession')
  const { draft, presetFallback } = useContainerStore('skillDeepLink')
  const skillsActive = useSkillsActive()
  const onRoute = isOnRouteConversation(useContainerStore('conversations'), routeId)
  const options = commands?.options ?? NO_OPTIONS
  // Not the composer's own availability: with input unlocked mid-turn the session still refuses a send.
  const ready = inputEnabled && connection.kind === 'connected' && !awaitingResponse && onRoute

  useEffect(() => {
    skillDeepLink.resolve(options, ready)
  }, [skillDeepLink, options, ready])

  return { draftRequest: draft, acknowledgeDraft: skillDeepLink.acknowledgeDraft, showPresets: !skillsActive || presetFallback }
}
