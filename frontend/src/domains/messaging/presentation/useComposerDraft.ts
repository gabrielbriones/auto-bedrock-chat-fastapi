import { useState, type FormEvent, type KeyboardEvent, type RefObject } from 'react'

import { MESSAGING_COPY } from '@/shared/copy/messaging'

import {
  activeSlashCommand,
  buildSlashCommand,
  freeTextParameter,
  initialValues,
  missingParameters,
  parameterFields,
  type SlashCommandOption,
  type SlashCommandValues,
} from '@/domains/messaging/domain/slash-command'
import {
  useSlashCommandMenu,
  type ComposerCommands,
  type SlashCommandMenuState,
} from '@/domains/messaging/presentation/useSlashCommandMenu'

const { slashMenu: COPY } = MESSAGING_COPY.composer

/** A chosen skill and the values filled in for its parameter fields. */
export type SkillDraft = {
  readonly option: SlashCommandOption
  readonly values: SlashCommandValues
}

/** With a skill chosen the textarea holds its free text, or is hidden or read-only when it takes none. */
export type TextareaMode = 'text' | 'hidden' | 'readonly'

export type ComposerDraft = {
  readonly menu: SlashCommandMenuState
  readonly skill: SkillDraft | null
  readonly ready: boolean
  readonly textareaMode: TextareaMode
  /** Replaces the default placeholder while a skill is chosen. */
  readonly placeholder: string | null
  readonly change: (text: string) => void
  readonly setValue: (name: string, value: string) => void
  readonly cancelSkill: () => void
  readonly handleKey: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean
  readonly submit: (event: FormEvent) => void
}

type DraftDeps = {
  readonly value: string
  readonly locked: boolean
  readonly commands: ComposerCommands | undefined
  readonly field: RefObject<HTMLTextAreaElement | null>
  readonly onChange: (text: string) => void
  readonly onSend: (text: string) => void
}

const NO_OPTIONS: readonly SlashCommandOption[] = []

const textareaModeOf = (skill: SkillDraft | null): TextareaMode => {
  if (skill === null || freeTextParameter(skill.option) !== null) {
    return 'text'
  }
  return parameterFields(skill.option).length > 0 ? 'hidden' : 'readonly'
}

const placeholderOf = (skill: SkillDraft | null): string | null => {
  if (skill === null) {
    return null
  }
  const freeText = freeTextParameter(skill.option)
  if (freeText === null) {
    return COPY.run(skill.option.name)
  }
  return freeText.placeholder || freeText.label || COPY.details
}

// The textarea re-renders (shown again, or emptied) before it can take focus.
const focusLater = (field: RefObject<HTMLTextAreaElement | null>) => requestAnimationFrame(() => field.current?.focus())

/**
 * The composer's draft: plain text, or a chosen skill whose parameters are filled in fields and
 * sent as `/name key=value ...` (the textarea then holds the skill's free text, if it takes any).
 */
export const useComposerDraft = ({ value, locked, commands, field, onChange, onSend }: DraftDeps): ComposerDraft => {
  const [skill, setSkill] = useState<SkillDraft | null>(null)
  const start = (option: SlashCommandOption) => {
    onChange('')
    setSkill({ option, values: initialValues(option) })
    focusLater(field)
  }
  const cancelSkill = () => {
    setSkill(null)
    focusLater(field)
  }
  const menu = useSlashCommandMenu(skill === null ? value : '', commands, start)
  const ready = skill === null ? value.trim() !== '' : missingParameters(skill.option, skill.values, value).length === 0

  return {
    menu,
    skill,
    ready,
    textareaMode: textareaModeOf(skill),
    placeholder: placeholderOf(skill),
    change: (text) => {
      const typed = skill === null ? activeSlashCommand(text, commands?.options ?? NO_OPTIONS) : null
      return typed !== null && text === `/${typed.name} ` ? start(typed) : onChange(text)
    },
    setValue: (name, next) => setSkill((current) => current && { ...current, values: { ...current.values, [name]: next } }),
    cancelSkill,
    handleKey: (event) => {
      if (menu.handleKey(event)) {
        return true
      }
      if (skill === null || !(event.key === 'Escape' || (event.key === 'Backspace' && value === ''))) {
        return false
      }
      event.preventDefault()
      cancelSkill()
      return true
    },
    submit: (event) => {
      event.preventDefault()
      if (locked || !ready) {
        return
      }
      onSend(skill === null ? value : buildSlashCommand(skill.option, skill.values, value))
      setSkill(null)
      field.current?.focus()
    },
  }
}
