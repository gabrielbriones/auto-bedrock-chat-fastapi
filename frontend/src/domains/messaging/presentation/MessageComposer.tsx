import { ArrowUp } from 'lucide-react'
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

import {
  composerKeyIntent,
  type ComposerAvailability,
} from '@/domains/messaging/presentation/composer-policy'

const { composer: COPY } = MESSAGING_COPY

/** FR-MSG-011: grow with the content up to here, then scroll. */
const MAX_HEIGHT_PX = 150

export type MessageComposerProps = {
  readonly value: string
  readonly availability: ComposerAvailability
  readonly awaitingResponse: boolean
  readonly onChange: (text: string) => void
  readonly onSend: (text: string) => void
}

const autosize = (field: HTMLTextAreaElement): void => {
  // Collapsing first is what lets this shrink as well as grow: `scrollHeight` never reports less
  // than the height already set.
  field.style.height = 'auto'
  field.style.height = `${Math.min(field.scrollHeight, MAX_HEIGHT_PX)}px`
  field.style.overflowY = field.scrollHeight > MAX_HEIGHT_PX ? 'auto' : 'hidden'
}

const insertNewline = (field: HTMLTextAreaElement, onChange: (text: string) => void): void => {
  const start = field.selectionStart
  const end = field.selectionEnd

  onChange(`${field.value.slice(0, start)}\n${field.value.slice(end)}`)
  // The controlled re-render would otherwise park the caret at the end of the draft.
  requestAnimationFrame(() => field.setSelectionRange(start + 1, start + 1))
}

const useComposerField = (
  value: string,
  locked: boolean,
): RefObject<HTMLTextAreaElement | null> => {
  const field = useRef<HTMLTextAreaElement>(null)
  const wasLocked = useRef(locked)

  useLayoutEffect(() => {
    if (field.current !== null) {
      autosize(field.current)
    }
  }, [value])

  // FR-MSG-012 "on unlock, focus returns to the composer" — including the unlock a recycled (stale
  // or dropped) turn produces, which is otherwise a dead end for a keyboard user.
  useEffect(() => {
    if (wasLocked.current && !locked) {
      field.current?.focus()
    }

    wasLocked.current = locked
  }, [locked])

  return field
}

type KeyDownDeps = {
  readonly locked: boolean
  readonly hasText: boolean
  readonly onChange: (text: string) => void
  readonly submit: (event: FormEvent) => void
}

const handleKeyDown = (
  event: KeyboardEvent<HTMLTextAreaElement>,
  { locked, hasText, onChange, submit }: KeyDownDeps,
): void => {
  const intent = composerKeyIntent(
    {
      key: event.key,
      shiftKey: event.shiftKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      altKey: event.altKey,
      isComposing: event.nativeEvent.isComposing,
    },
    { locked, hasText },
  )

  switch (intent) {
    case 'send':
      submit(event)
      return
    case 'newline':
      // Shift+Enter inserts one natively; Ctrl/Meta/Alt+Enter do not, so they are spliced in.
      if (!event.shiftKey) {
        event.preventDefault()
        insertNewline(event.currentTarget, onChange)
      }
      return
    case 'ignore':
      event.preventDefault()
      return
    case 'pass':
  }
}

function SendButton({ disabled }: { readonly disabled: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="submit"
            size="icon"
            disabled={disabled}
            aria-label={COPY.send}
            className="size-11 shrink-0 self-end rounded-lg shadow-sm disabled:bg-muted disabled:text-muted-foreground disabled:opacity-100"
          />
        }
      >
        <ArrowUp className="size-5" aria-hidden="true" />
      </TooltipTrigger>
      <TooltipContent>{COPY.send}</TooltipContent>
    </Tooltip>
  )
}

export function MessageComposer({
  value,
  availability,
  awaitingResponse,
  onChange,
  onSend,
}: MessageComposerProps) {
  const locked = !availability.enabled
  const hasText = value.trim() !== ''
  const field = useComposerField(value, locked)
  const hintId = useId()

  const submit = (event: FormEvent) => {
    event.preventDefault()

    if (locked || !hasText) {
      return
    }

    onSend(value)
    field.current?.focus()
  }

  return (
    // FR-SHELL-018: <main> is the only scroller, so the composer stays put by sticking to its bottom
    // edge rather than by owning a scroller of its own.
    <form onSubmit={submit} className="sticky bottom-0 z-10 shrink-0 bg-background px-3 pb-3 pt-2 sm:px-4 sm:pb-4">
      <div className="flex items-end gap-2 rounded-xl border border-input bg-background p-2 shadow-sm transition-colors focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20 forced-colors:focus-within:outline-2 forced-colors:focus-within:outline-offset-2 forced-colors:focus-within:outline-ring dark:bg-input/30">
        <Textarea
          ref={field}
          rows={1}
          value={value}
          disabled={locked}
          aria-label={COPY.label}
          aria-describedby={hintId}
          placeholder={awaitingResponse ? COPY.waitingPlaceholder : COPY.placeholder}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => handleKeyDown(event, { locked, hasText, onChange, submit })}
          className="min-h-11 max-h-[150px] min-w-0 flex-1 resize-none rounded-none border-0 bg-transparent px-2 py-3 text-sm leading-5 shadow-none focus-visible:ring-0 disabled:bg-transparent dark:bg-transparent dark:disabled:bg-transparent"
        />
        <SendButton disabled={locked || !hasText} />
      </div>
      <p id={hintId} className={availability.reason === null ? 'sr-only' : 'px-2 pt-2 text-xs text-muted-foreground'}>
        {availability.reason ?? COPY.hint}
      </p>
    </form>
  )
}
