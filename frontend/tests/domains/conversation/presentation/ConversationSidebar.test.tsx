import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from '@jest/globals'
import { axe } from 'jest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { fakeContainer } from '../../../app/bootstrap/container.fixture'
import { CONVERSATION_COPY } from '@/shared/copy/conversation'

import {
  createHarness,
  type HarnessOptions,
} from '../application/conversation.store.fixture'
import { aConversation, anEvent, id } from '../domain/conversation.fixture'
import { ConversationSidebar } from '@/domains/conversation/presentation/ConversationSidebar'

const renderSidebar = (options: HarnessOptions = {}) => {
  const harness = createHarness(options)
  harness.gateway.emit(
    anEvent.listed([aConversation('a', 9, 'Job 42'), aConversation('b', 0, null)]),
  )
  harness.gateway.calls.length = 0

  const container = fakeContainer({ conversations: harness.store })

  const view = render(
    <ContainerContext.Provider value={container}>
      <ConversationSidebar />
    </ContainerContext.Provider>,
  )

  return {
    ...harness,
    view,
    // Frames arrive from outside React, so the re-render they cause has to be flushed explicitly.
    emit: (event: Parameters<typeof harness.gateway.emit>[0]) => {
      act(() => {
        harness.gateway.emit(event)
      })
    },
  }
}

const itemFor = (title: string) =>
  screen.getByRole('button', { name: title }).closest('li') as HTMLElement

describe('ConversationSidebar', () => {
  it('renders nothing when the roster is not available (FR-CONV-001)', () => {
    renderSidebar({ persistenceEnabled: false })

    expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  })

  // FR-CONV-014.
  it('renders a null title as the untitled fallback', () => {
    renderSidebar()

    expect(screen.getByRole('button', { name: CONVERSATION_COPY.untitled })).toBeInTheDocument()
  })

  // FR-CONV-013.
  it('lists conversations newest first', () => {
    renderSidebar()

    const titles = screen.getAllByRole('listitem').map((item) => item.textContent)
    expect(titles[0]).toContain('Job 42')
  })

  // NFR-A11Y-002: every control of an item is reachable in order, with nothing focus-trapped.
  it('reaches the whole first item by keyboard alone', async () => {
    renderSidebar()
    const user = userEvent.setup()
    const reached: (string | null)[] = []

    for (let step = 0; step < 4; step += 1) {
      await user.tab()
      const active = document.activeElement
      reached.push(active?.getAttribute('aria-label') ?? active?.textContent ?? null)
    }

    expect(reached).toEqual([
      CONVERSATION_COPY.bulk.selectAll,
      CONVERSATION_COPY.sidebar.newChat,
      CONVERSATION_COPY.item.select('Job 42'),
      'Job 42',
    ])
  })

  // FR-CONV-015: the title is a real button, so Enter and Space activate it with no key handler.
  it.each(['{Enter}', ' '])('opens a conversation from the keyboard with %s', async (key) => {
    const harness = renderSidebar()
    const user = userEvent.setup()

    act(() => {
      screen.getByRole('button', { name: 'Job 42' }).focus()
    })
    await user.keyboard(key)

    expect(harness.gateway.calls).toContainEqual(['load', id('a')])
  })

  // FR-CONV-015: activating the row must not fire from the checkbox.
  it('does not open the conversation when its checkbox is toggled', async () => {
    const harness = renderSidebar()
    const user = userEvent.setup()

    await user.click(
      within(itemFor('Job 42')).getByRole('checkbox', {
        name: CONVERSATION_COPY.item.select('Job 42'),
      }),
    )

    expect(harness.gateway.methods()).not.toContain('load')
    expect(harness.store.getSnapshot().selection.has(id('a'))).toBe(true)
  })

  // FR-CONV-015: nor from the options control.
  it('does not open the conversation when its options menu is opened', async () => {
    const harness = renderSidebar()
    const user = userEvent.setup()

    await user.click(
      screen.getByRole('button', { name: CONVERSATION_COPY.item.options('Job 42') }),
    )

    expect(await screen.findByRole('menuitem', { name: CONVERSATION_COPY.item.rename })).toBeVisible()
    for (const name of [CONVERSATION_COPY.item.rename, CONVERSATION_COPY.item.delete]) {
      expect(screen.getByRole('menuitem', { name }).querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    }
    expect(harness.gateway.methods()).not.toContain('load')
  })

  it('opens the options menu with the keyboard without opening the conversation', async () => {
    const harness = renderSidebar()
    const user = userEvent.setup()

    act(() => { screen.getByRole('button', { name: 'Job 42' }).focus() })
    await user.tab()
    expect(screen.getByRole('button', { name: CONVERSATION_COPY.item.options('Job 42') })).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('menuitem', { name: CONVERSATION_COPY.item.rename })).toBeVisible()
    expect(harness.gateway.methods()).not.toContain('load')
  })

  it('keeps the full title available when the row is truncated', () => {
    const harness = renderSidebar()
    const title = 'Low-IPC Network Test with a very long conversation title'
    harness.emit(anEvent.listed([aConversation('a', 9, title)]))

    expect(screen.getByRole('button', { name: title })).toHaveAttribute('title', title)
  })

  // FR-CONV-006: the indeterminate state is real, not a checked/unchecked approximation.
  it('reports a partial selection as indeterminate on the select-all control', async () => {
    renderSidebar()
    const user = userEvent.setup()
    const selectAll = screen.getByRole('checkbox', { name: CONVERSATION_COPY.bulk.selectAll })

    await user.click(
      within(itemFor('Job 42')).getByRole('checkbox', {
        name: CONVERSATION_COPY.item.select('Job 42'),
      }),
    )

    expect(selectAll).toHaveAttribute('aria-checked', 'mixed')
  })

  it('hides the bulk bar at zero selection and shows the count once something is selected', async () => {
    renderSidebar()
    const user = userEvent.setup()

    expect(screen.queryByText(CONVERSATION_COPY.bulk.selected(1))).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: CONVERSATION_COPY.bulk.selectAll }))

    expect(screen.getByText(CONVERSATION_COPY.bulk.selected(2))).toBeInTheDocument()
  })

  // FR-CONV-019: the transient error stays put instead of expiring with a toast.
  it('shows an unavailable-history notice in place', () => {
    const harness = renderSidebar()

    harness.emit(anEvent.error('conversation_history_unavailable', 'History is down'))

    expect(screen.getByRole('status')).toHaveTextContent('History is down')
  })

  // NFR-A11Y-002.
  it('has no axe violations', async () => {
    const { view } = renderSidebar()

    const results = await axe(view.container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
