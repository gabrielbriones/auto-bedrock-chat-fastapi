import { useEffect } from 'react'

import { cn } from '@/lib/utils'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

import type { SlashCommandOption } from '@/domains/messaging/domain/slash-command'
import type { SlashCommandMenuState } from '@/domains/messaging/presentation/useSlashCommandMenu'

const { slashMenu: COPY } = MESSAGING_COPY.composer

type ItemProps = {
  readonly id: string
  readonly option: SlashCommandOption
  readonly active: boolean
  readonly onHighlight: () => void
  readonly onChoose: () => void
}

// `onMouseDown` is cancelled so a click never blurs the textarea.
function SlashCommandItem({ id, option, active, onHighlight, onChoose }: ItemProps) {
  return (
    <li
      id={id}
      role="option"
      aria-selected={active}
      onMouseDown={(event) => event.preventDefault()}
      onMouseEnter={onHighlight}
      onClick={onChoose}
      className={cn(
        'flex cursor-pointer flex-col gap-0.5 rounded-lg px-3 py-2 text-sm',
        active && 'bg-accent text-accent-foreground',
      )}
    >
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="font-mono font-medium">/{option.name}</span>
        <span className="truncate text-muted-foreground">{option.title}</span>
      </span>
      {option.description !== '' && <span className="line-clamp-2 text-xs text-muted-foreground">{option.description}</span>}
      <span className="truncate font-mono text-xs text-muted-foreground">{option.usage}</span>
    </li>
  )
}

function SlashCommandList({ menu }: { readonly menu: SlashCommandMenuState }) {
  const activeId = menu.optionId(menu.activeIndex)

  useEffect(() => {
    document.getElementById(activeId)?.scrollIntoView?.({ block: 'nearest' })
  }, [activeId])

  return (
    <ul
      id={menu.listboxId}
      role="listbox"
      aria-label={COPY.label}
      className="absolute inset-x-0 bottom-full z-20 mb-2 max-h-72 overflow-auto rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-lg"
    >
      {menu.matches.map((option, index) => (
        <SlashCommandItem
          key={option.name}
          id={menu.optionId(index)}
          option={option}
          active={index === menu.activeIndex}
          onHighlight={() => menu.highlight(index)}
          onChoose={() => menu.choose(option)}
        />
      ))}
    </ul>
  )
}

// The textarea keeps focus and points at the highlighted option with `aria-activedescendant`.
// Once a known command is typed, its usage line replaces the menu.
export function SlashCommandMenu({ menu }: { readonly menu: SlashCommandMenuState }) {
  return (
    <>
      <span className="sr-only" aria-live="polite">
        {menu.open ? COPY.results(menu.matches.length) : ''}
      </span>
      {menu.running !== null && (
        <p className="truncate px-2 pt-1 font-mono text-xs text-muted-foreground">
          <span className="sr-only">{COPY.usage} </span>
          {menu.running.usage}
        </p>
      )}
      {menu.open && <SlashCommandList menu={menu} />}
    </>
  )
}
