import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AppShell } from '@/app/layouts/AppShell'
import { SHELL } from '@/shared/copy/shell'

const scrollContainers = (container: HTMLElement) =>
  container.querySelectorAll('.overflow-y-auto')

describe('AppShell', () => {
  it('exposes exactly one scroll container, so the document itself never scrolls', () => {
    const { container } = render(
      <AppShell header={<header>head</header>} sidebar={<nav>side</nav>} footer={<div>foot</div>}>
        <p>body</p>
      </AppShell>,
    )

    expect(scrollContainers(container)).toHaveLength(1)
    expect(container.querySelector('[data-slot="app-shell"]')).toHaveClass('overflow-hidden')
  })

  it('offers the skip link as the first focusable element and moves focus to main', async () => {
    const user = userEvent.setup()

    render(
      <AppShell header={<header>head</header>}>
        <p>body</p>
      </AppShell>,
    )

    await user.tab()

    const skipLink = screen.getByRole('link', { name: SHELL.skipToContent })
    expect(skipLink).toHaveFocus()

    await user.click(skipLink)

    const main = screen.getByRole('main')
    expect(main).toHaveFocus()
    expect(main).toHaveAttribute('tabindex', '0')
    expect(main).toHaveClass('focus-visible:outline-2', 'focus-visible:outline-ring')
  })

  it('names the main landmark so screen-reader users can jump to it', () => {
    render(
      <AppShell mainLabel="Transcript">
        <p>body</p>
      </AppShell>,
    )

    expect(screen.getByRole('main', { name: 'Transcript' })).toBeInTheDocument()
  })
})
