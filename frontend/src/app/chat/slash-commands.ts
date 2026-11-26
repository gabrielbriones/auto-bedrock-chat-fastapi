import { useCallback, useEffect, useMemo } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
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
  }))

/** The enabled skills as composer commands, re-read when input opens and on composer focus once stale. */
export const useSkillCommands = (): ComposerCommands => {
  const { skillCatalog } = useContainer()
  const { inputEnabled } = useContainerStore('identity')
  const { entries } = useContainerStore('skillCatalog')
  const options = useMemo(() => toSlashCommandOptions(entries), [entries])
  const onFocus = useCallback(() => void skillCatalog.refresh(), [skillCatalog])

  // Signing in can be what makes the catalog readable.
  useEffect(() => {
    if (inputEnabled) {
      onFocus()
    }
  }, [inputEnabled, onFocus])

  return useMemo(() => ({ options, onFocus }), [options, onFocus])
}
