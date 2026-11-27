import { useId, useState, type KeyboardEvent } from 'react'

import {
  activeSlashCommand,
  matchSlashCommands,
  slashQuery,
  type SlashCommandOption,
} from '@/domains/messaging/domain/slash-command'

/** Slash commands offered by the composer, supplied from `src/app`. */
export type ComposerCommands = {
  readonly options: readonly SlashCommandOption[]
  /** Called whenever the composer gains focus, e.g. to refresh `options`. */
  readonly onFocus: () => void
  /** The commands could not be read; a typed command is still sent, the server parses it. */
  readonly unavailable?: boolean
}

export type SlashCommandMenuState = {
  readonly open: boolean
  readonly matches: readonly SlashCommandOption[]
  readonly activeIndex: number
  /** The known command the draft is running, for the usage hint; `null` while the menu is open. */
  readonly running: SlashCommandOption | null
  /** A command is being typed but the list could not be loaded. */
  readonly unavailable: boolean
  readonly listboxId: string
  readonly optionId: (index: number) => string
  /** Spread on the textarea: it stays focused and points at the highlighted option. */
  readonly fieldProps: {
    readonly 'aria-autocomplete': 'list' | undefined
    readonly 'aria-controls': string | undefined
    readonly 'aria-activedescendant': string | undefined
    readonly onFocus: (() => void) | undefined
  }
  /** Handles a composer keystroke; `true` when the menu consumed it. */
  readonly handleKey: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean
  readonly choose: (option: SlashCommandOption) => void
  readonly highlight: (index: number) => void
}

const NO_OPTIONS: readonly SlashCommandOption[] = []

const wrap = (index: number, length: number): number => (index + length) % length

type KeyActions = {
  readonly open: boolean
  readonly current: number
  readonly matches: readonly SlashCommandOption[]
  readonly highlight: (index: number) => void
  readonly choose: (option: SlashCommandOption) => void
  readonly dismiss: () => void
}

const keyHandler =
  ({ open, current, matches, highlight, choose, dismiss }: KeyActions) =>
  (event: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    const selected = matches[current]
    // Modified keys (Shift/Ctrl/Meta/Alt+Enter insert a newline) stay with the composer.
    const modified = event.shiftKey || event.ctrlKey || event.metaKey || event.altKey
    if (!open || selected === undefined || event.nativeEvent.isComposing || modified) {
      return false
    }
    const actions: Readonly<Record<string, () => void>> = {
      ArrowDown: () => highlight(wrap(current + 1, matches.length)),
      ArrowUp: () => highlight(wrap(current - 1, matches.length)),
      Enter: () => choose(selected),
      Tab: () => choose(selected),
      Escape: dismiss,
    }
    const action = actions[event.key]
    if (action === undefined) {
      return false
    }
    event.preventDefault()
    action()
    return true
  }

export const useSlashCommandMenu = (
  value: string,
  commands: ComposerCommands | undefined,
  onChoose: (option: SlashCommandOption) => void,
): SlashCommandMenuState => {
  const options = commands?.options ?? NO_OPTIONS
  const listboxId = useId()
  const query = slashQuery(value)
  // The highlight belongs to the query it was made for, so typing more starts again at the top.
  const [cursor, setCursor] = useState({ query, index: 0 })
  // Escape closes the menu for this exact draft; typing reopens it.
  const [dismissedFor, setDismissedFor] = useState<string | null>(null)
  const matches = query === null ? [] : matchSlashCommands(options, query)
  const open = matches.length > 0 && dismissedFor !== value
  const current = Math.min(cursor.query === query ? cursor.index : 0, Math.max(matches.length - 1, 0))
  const optionId = (index: number) => `${listboxId}-option-${index}`
  const highlight = (index: number) => setCursor({ query, index })
  const choose = (option: SlashCommandOption) => {
    setDismissedFor(null)
    onChoose(option)
  }

  return {
    open,
    matches,
    activeIndex: current,
    running: open ? null : activeSlashCommand(value, options),
    unavailable: commands?.unavailable === true && query !== null && matches.length === 0,
    listboxId,
    optionId,
    fieldProps: {
      'aria-autocomplete': options.length > 0 ? 'list' : undefined,
      'aria-controls': open ? listboxId : undefined,
      'aria-activedescendant': open ? optionId(current) : undefined,
      onFocus: commands?.onFocus,
    },
    handleKey: keyHandler({ open, current, matches, highlight, choose, dismiss: () => setDismissedFor(value) }),
    choose,
    highlight,
  }
}
