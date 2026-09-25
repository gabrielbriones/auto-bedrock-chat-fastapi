import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { type ReactNode } from 'react'
import { describe, expect, it, jest, afterEach, beforeEach } from '@jest/globals'
import { axe } from 'jest-axe'

import { MAIN_SCROLL_CONTAINER_ID } from '@/components/ui/composed/scroll-container'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import { Instant } from '@/shared/kernel/instant'
import { messageId } from '@/shared/kernel/branded'
import { isOk } from '@/shared/kernel/result'

import type { TranscriptEntry } from '@/domains/messaging/domain/transcript'
import { Transcript } from '@/domains/messaging/presentation/Transcript'

const atResult = Instant.fromEpochMilliseconds(0)
if (!isOk(atResult)) {
  throw new Error('Expected test instant to be valid')
}
const at = atResult.value

const entry = (key: string, raw: string, overrides: Partial<TranscriptEntry> = {}): TranscriptEntry => ({
  key,
  message: { id: messageId(key), role: 'user', raw, at, seq: 1 },
  pending: false,
  streaming: false,
  transient: false,
  progress: false,
  interrupted: false,
  activity: null,
  ...overrides,
})

const manyEntries = (count: number): TranscriptEntry[] =>
  Array.from({ length: count }, (_, index) => entry(`m-${index}`, `message ${index}`))

// jsdom computes no layout, so the metrics stick-to-bottom reads are all zero and `scrollTop` is
// a no-op setter. These are the only three values the hook touches.
const giveLayout = (element: HTMLElement, scrollHeight: number, clientHeight: number): void => {
  let scrollTop = 0

  Object.defineProperty(element, 'scrollHeight', { configurable: true, get: () => scrollHeight })
  Object.defineProperty(element, 'clientHeight', { configurable: true, get: () => clientHeight })
  // The virtualiser sizes its viewport from the offset box, which jsdom always reports as zero.
  Object.defineProperty(element, 'offsetHeight', { configurable: true, get: () => clientHeight })
  Object.defineProperty(element, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(element, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value: number) => {
      scrollTop = value
    },
  })
}

// FR-DS-008: the shell's <main> is the scroll container, and the transcript drives it from inside.
function Scroller({ children }: { readonly children: ReactNode }) {
  return (
    <main id={MAIN_SCROLL_CONTAINER_ID} data-testid="scroller">
      {children}
    </main>
  )
}

const renderTranscript = (entries: readonly TranscriptEntry[], presetArea?: ReactNode) => {
  const view = render(
    <Scroller>
      <Transcript entries={[]} welcome="How can I help?" presetArea={presetArea} />
    </Scroller>,
  )

  const scroller = screen.getByTestId('scroller')
  giveLayout(scroller, 1000, 200)

  const show = (next: readonly TranscriptEntry[]) => {
    view.rerender(
      <Scroller>
        <Transcript entries={next} welcome="How can I help?" presetArea={presetArea} />
      </Scroller>,
    )
  }

  show(entries)

  return { ...view, scroller, show }
}

const liveRegion = (container: HTMLElement): string =>
  container.querySelector('[aria-live="polite"]')?.textContent ?? ''

describe('Transcript scroll behaviour (FIX-12)', () => {
  it('follows the newest message while the reader is at the bottom', () => {
    const { scroller } = renderTranscript(manyEntries(3))

    expect(scroller.scrollTop).toBe(1000)
    expect(screen.queryByRole('button', { name: /Jump to latest/ })).not.toBeInTheDocument()
  })

  it('keeps the viewport still, counts unread and re-arms on jump when scrolled away', async () => {
    const user = userEvent.setup()
    const { scroller, show } = renderTranscript(manyEntries(1))

    act(() => {
      scroller.scrollTop = 100
      fireEvent.scroll(scroller)
    })

    show(manyEntries(3))

    expect(scroller.scrollTop).toBe(100)

    const jump = screen.getByRole('button', {
      name: MESSAGING_COPY.transcript.jumpToLatestUnread(2),
    })
    await user.click(jump)

    expect(scroller.scrollTop).toBe(1000)
    expect(screen.queryByRole('button', { name: /Jump to latest/ })).not.toBeInTheDocument()
  })
})

describe('Transcript virtualisation (NFR-PERF-005)', () => {
  // The virtualiser measures rows with `offsetHeight`, which jsdom always reports as zero; a row
  // that shrinks on every measurement makes it widen its range forever. Give rows a plausible
  // height so the measurement loop settles the way it does in a browser.
  const OFFSET_HEIGHT = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight')

  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get: () => 96,
    })
  })

  afterEach(() => {
    if (OFFSET_HEIGHT !== undefined) {
      Object.defineProperty(HTMLElement.prototype, 'offsetHeight', OFFSET_HEIGHT)
    }
  })

  it('keeps the rendered bubble count bounded for a long transcript', () => {
    const { container } = renderTranscript(manyEntries(1000))

    const rendered = screen.getAllByRole('article')
    expect(rendered.length).toBeGreaterThan(0)
    expect(rendered.length).toBeLessThan(100)
    // FR-DS-008: virtualising must not smuggle in a second scroll container.
    expect(container.querySelectorAll('.overflow-y-auto')).toHaveLength(0)
  })

  it('renders every bubble below the virtualisation threshold', () => {
    renderTranscript(manyEntries(12))

    expect(screen.getAllByRole('article')).toHaveLength(12)
  })
})

describe('Transcript live region (NFR-A11Y-006)', () => {
  it('announces a status while a turn streams, then the settled answer', async () => {
    const { container, show } = renderTranscript([
      entry('t-1', 'Hel', {
        message: { id: null, role: 'assistant', raw: 'Hel', at, seq: 2 },
        streaming: true,
        pending: true,
      }),
    ])

    await waitFor(() => {
      expect(liveRegion(container)).toBe(MESSAGING_COPY.transcript.responding)
    })

    show([
      entry('t-1', 'Hello there', {
        message: { id: messageId('m-1'), role: 'assistant', raw: 'Hello there', at, seq: 2 },
      }),
    ])

    await waitFor(
      () => {
        expect(liveRegion(container)).toBe('Hello there')
      },
      { timeout: 3000 },
    )
  })

  it('says nothing about a message the reader typed themselves', () => {
    const { container } = renderTranscript(manyEntries(1))

    expect(liveRegion(container)).toBe('')
  })
})

describe('Transcript welcome state (FR-MSG-031)', () => {
  it('shows the supplied presets in place of canned suggestions', async () => {
    const user = userEvent.setup()
    const activate = jest.fn()
    render(
      <Scroller>
        <Transcript
          entries={[]}
          welcome="How can I help?"
          presetArea={<button onClick={activate}>Analyze workload</button>}
        />
      </Scroller>,
    )

    const preset = screen.getByRole('button', { name: 'Analyze workload' })
    expect(screen.getByRole('region', { name: MESSAGING_COPY.welcome.label })).toContainElement(preset)
    expect(screen.queryByRole('button', { name: MESSAGING_COPY.welcome.suggestions[0]! })).not.toBeInTheDocument()
    await user.click(preset)
    expect(activate).toHaveBeenCalledTimes(1)
  })

  it('disappears as soon as the transcript has content', () => {
    const { show } = renderTranscript([], <button>Analyze workload</button>)

    expect(screen.getByText('How can I help?')).toBeInTheDocument()

    show(manyEntries(1))

    expect(screen.queryByText('How can I help?')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Analyze workload' })).not.toBeInTheDocument()
    expect(screen.getByRole('article')).toBeInTheDocument()

    show([])

    expect(screen.getByText('How can I help?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analyze workload' })).toBeInTheDocument()
  })

  it('has no axe violations', async () => {
    const { container } = renderTranscript([])

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
