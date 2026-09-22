import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'

import { MESSAGING_COPY } from '@/shared/copy/messaging'

import { MessageComposer } from '@/domains/messaging/presentation/MessageComposer'
import type { ComposerAvailability } from '@/domains/messaging/presentation/composer-policy'

const { composer: COPY } = MESSAGING_COPY

const OPEN: ComposerAvailability = { enabled: true, reason: null }
const LOCKED: ComposerAvailability = { enabled: false, reason: COPY.disabled.responding }

const LINE_HEIGHT_PX = 20

// jsdom has no layout: `scrollHeight` is always 0, so `autosize` would compute a constant. Deriving
// it from the line count gives the hook something real to cap.
let originalScrollHeight: PropertyDescriptor | undefined

beforeAll(() => {
  originalScrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight')

  Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollHeight', {
    configurable: true,
    get(this: HTMLTextAreaElement) {
      return this.value.split('\n').length * LINE_HEIGHT_PX
    },
  })
})

afterAll(() => {
  delete (HTMLTextAreaElement.prototype as { scrollHeight?: number }).scrollHeight

  if (originalScrollHeight !== undefined) {
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', originalScrollHeight)
  }
})

const Harness = ({
  availability = OPEN,
  awaitingResponse = false,
  initial = '',
  onSend = vi.fn(),
}: {
  readonly availability?: ComposerAvailability
  readonly awaitingResponse?: boolean
  readonly initial?: string
  readonly onSend?: (text: string) => void
}) => {
  const [draft, setDraft] = useState(initial)

  return (
    <MessageComposer
      value={draft}
      availability={availability}
      awaitingResponse={awaitingResponse}
      onChange={setDraft}
      onSend={(text) => {
        onSend(text)
        setDraft('')
      }}
    />
  )
}

const field = () => screen.getByRole('textbox', { name: COPY.label })

describe('MessageComposer keyboard matrix (FR-MSG-010)', () => {
  it('sends on a bare Enter', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<Harness onSend={onSend} />)

    await user.type(field(), 'analyse job 42{Enter}')

    expect(onSend).toHaveBeenCalledExactlyOnceWith('analyse job 42')
    expect(field()).toHaveValue('')
  })

  it('inserts a newline on Shift+Enter without sending', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<Harness onSend={onSend} />)

    await user.type(field(), 'first{Shift>}{Enter}{/Shift}second')

    expect(onSend).not.toHaveBeenCalled()
    expect(field()).toHaveValue('first\nsecond')
  })

  // The browser inserts nothing for these, so the composer has to splice the newline in itself.
  it.each([
    ['Control', '{Control>}{Enter}{/Control}'],
    ['Meta', '{Meta>}{Enter}{/Meta}'],
    ['Alt', '{Alt>}{Enter}{/Alt}'],
  ])('inserts a newline on %s+Enter without sending', async (_name, chord) => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<Harness initial="first" onSend={onSend} />)

    await user.click(field())
    await user.keyboard(chord)

    expect(onSend).not.toHaveBeenCalled()
    await waitFor(() => {
      expect(field()).toHaveValue('first\n')
    })
  })

  it('splices the newline at the caret rather than at the end', async () => {
    const user = userEvent.setup()
    render(<Harness initial="abcd" />)

    const textarea = field() as HTMLTextAreaElement
    await user.click(textarea)
    textarea.setSelectionRange(2, 2)
    await user.keyboard('{Control>}{Enter}{/Control}')

    await waitFor(() => {
      expect(textarea).toHaveValue('ab\ncd')
    })
  })

  it('refuses to send a whitespace-only draft (FR-MSG-025)', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<Harness onSend={onSend} />)

    await user.type(field(), '   {Enter}')

    expect(onSend).not.toHaveBeenCalled()
    expect(field()).toHaveValue('   ')
    expect(screen.getByRole('button', { name: COPY.send })).toBeDisabled()
  })

  it('never sends the Enter that commits an IME candidate', async () => {
    const onSend = vi.fn()
    render(<Harness initial="日本" onSend={onSend} />)

    const textarea = field()
    textarea.focus()
    fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true })

    expect(onSend).not.toHaveBeenCalled()
  })

  it('leaves Escape to its ancestors so a surrounding dialog still closes', async () => {
    const user = userEvent.setup()
    const onEscape = vi.fn()
    render(
      <div
        onKeyDown={(event) => {
          if (event.key === 'Escape') onEscape()
        }}
      >
        <Harness initial="draft" />
      </div>,
    )

    await user.click(field())
    await user.keyboard('{Escape}')

    expect(onEscape).toHaveBeenCalledOnce()
    expect(field()).toHaveValue('draft')
  })
})

describe('MessageComposer autosize (FR-MSG-011)', () => {
  it('keeps the icon send control fixed-size as the draft grows', () => {
    render(<Harness />)
    const send = screen.getByRole('button', { name: COPY.send })

    expect(send).toHaveClass('size-11', 'shrink-0', 'self-end')
    expect(send).toHaveTextContent('')
    fireEvent.change(field(), { target: { value: 'line\n'.repeat(20) } })

    expect(field()).toHaveStyle({ height: '150px', overflowY: 'auto' })
    expect(send).toHaveClass('size-11', 'shrink-0', 'self-end')
    expect(send).toBeEnabled()
  })

  it('sends through the icon button and clears the draft', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<Harness initial="analyse job 42" onSend={onSend} />)

    await user.click(screen.getByRole('button', { name: COPY.send }))

    expect(onSend).toHaveBeenCalledExactlyOnceWith('analyse job 42')
    expect(field()).toHaveValue('')
    expect(field()).toHaveFocus()
  })

  it('grows with the content up to the cap, then scrolls', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    const textarea = field() as HTMLTextAreaElement
    await user.type(textarea, 'one{Shift>}{Enter}{/Shift}two')

    expect(textarea.style.height).toBe('40px')
    expect(textarea.style.overflowY).toBe('hidden')

    render(<Harness initial={Array.from({ length: 20 }, (_, index) => index).join('\n')} />)
    const tall = screen.getAllByRole('textbox', { name: COPY.label }).at(-1) as HTMLTextAreaElement

    expect(tall.style.height).toBe('150px')
    expect(tall.style.overflowY).toBe('auto')
  })

  it('resets to its base height after sending', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    const textarea = field() as HTMLTextAreaElement
    await user.type(textarea, 'one{Shift>}{Enter}{/Shift}two')
    expect(textarea.style.height).toBe('40px')

    await user.keyboard('{Enter}')

    expect(textarea).toHaveValue('')
    expect(textarea.style.height).toBe('20px')
  })
})

describe('MessageComposer locking and enablement (FR-MSG-012/012a)', () => {
  it('states why it is closed rather than being inertly greyed out', () => {
    render(<Harness availability={LOCKED} awaitingResponse />)

    expect(field()).toBeDisabled()
    expect(field()).toHaveAccessibleDescription(COPY.disabled.responding)
    expect(field()).toHaveAttribute('placeholder', COPY.waitingPlaceholder)
    expect(screen.getByRole('button', { name: COPY.send })).toBeDisabled()
  })

  it('keeps the keyboard hint accessible without a visible footer while open', () => {
    render(<Harness />)

    expect(field()).toHaveAccessibleDescription(COPY.hint)
    expect(screen.getByText(COPY.hint)).toHaveClass('sr-only')
  })

  it.each([
    ['unauthenticated', COPY.disabled.unauthenticated],
    ['offline', COPY.disabled.offline],
  ])('states the %s reason', (_name, reason) => {
    render(<Harness availability={{ enabled: false, reason }} />)

    expect(field()).toHaveAccessibleDescription(reason)
    expect(screen.getByText(reason)).not.toHaveClass('sr-only')
  })

  // FR-MSG-012, and the XMGPLAT-11472 regression: a turn that resolves — by answer, failure, or by
  // being recycled after staleness — must hand the keyboard back.
  it.each(['answered', 'recycled'])('returns focus when an %s turn unlocks it', () => {
    const view = render(<Harness availability={LOCKED} awaitingResponse />)

    expect(field()).not.toHaveFocus()

    view.rerender(<Harness availability={OPEN} />)

    expect(field()).toHaveFocus()
  })

  it('does not steal focus while it stays open', async () => {
    const user = userEvent.setup()
    const view = render(
      <>
        <button type="button">elsewhere</button>
        <Harness />
      </>,
    )

    const elsewhere = screen.getByRole('button', { name: 'elsewhere' })
    await user.click(elsewhere)

    view.rerender(
      <>
        <button type="button">elsewhere</button>
        <Harness />
      </>,
    )

    expect(elsewhere).toHaveFocus()
  })
})
