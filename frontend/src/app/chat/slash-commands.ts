import { useCallback, useEffect, useMemo } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import type { SkillDeepLinkDraft } from '@/app/chat/skill-deep-link'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import type { SlashCommandOption } from '@/domains/messaging/domain/public'
import { composerAvailability } from '@/domains/messaging/presentation/composer-policy'
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

/** Runs a `?skill=` (or legacy `?prompt=`) link once the catalog and composer allow; returns its draft. */
export const useSkillDeepLink = (commands: ComposerCommands | undefined): SkillDeepLinkDraft | null => {
  const { bootstrap, skillDeepLink } = useContainer()
  const { inputEnabled } = useContainerStore('identity')
  const { connection, awaitingResponse } = useContainerStore('chatSession')
  const draft = useContainerStore('skillDeepLink')
  const options = commands?.options ?? NO_OPTIONS
  const { enabled } = composerAvailability(
    {
      inputEnabled,
      connected: connection.kind === 'connected',
      awaitingResponse,
      lockWhileResponding: bootstrap.lockInputWhileResponding,
    },
    MESSAGING_COPY.composer.disabled,
  )

  useEffect(() => {
    skillDeepLink.resolve(options, enabled)
  }, [skillDeepLink, options, enabled])

  return draft
}
