import { useState } from 'react'
import { describe, expect, it } from '@jest/globals'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ReviewDrawer } from '@/domains/review/presentation/ReviewDrawer'
import { REVIEW_COPY } from '@/shared/copy/review'

import { anEntry } from '../domain/feedback-entry.fixture'

const renderDrawer = (aiResponse: string) =>
  render(
    <ReviewDrawer
      open
      activeEntry={anEntry({ aiResponse })}
      detailStatus="ready"
      detailProblem={null}
      mutationPending={false}
      saveProblem={null}
      synthesisPhase={null}
      synthesisPending={false}
      synthesisProblem={null}
      onClose={() => {}}
      onSave={() => {}}
      onSynthesize={() => {}}
      onRollback={() => {}}
    />,
  )

describe('ReviewDrawer', () => {
  it('renders history through the shared sanitizer', async () => {
    const { container } = renderDrawer(
      '<script>alert(1)</script><iframe src="https://evil.example">frame</iframe><p>safe</p>',
    )

    await screen.findByRole('dialog', { name: REVIEW_COPY.drawer.title })
    expect(container.querySelector('script, iframe')).toBeNull()
    expect(screen.queryByText('alert(1)')).not.toBeInTheDocument()
    expect(screen.queryByText('frame')).not.toBeInTheDocument()
    expect(screen.getByText('safe')).toBeInTheDocument()
  })

  it('uses a wide drawer with a large scrollable conversation preview', async () => {
    renderDrawer('Assistant response')

    const dialog = await screen.findByRole('dialog', { name: REVIEW_COPY.drawer.title })
    const message = screen.getByText('Assistant response')
    const history = message.closest('div.overflow-y-auto')

    expect(dialog).toHaveClass('data-[side=right]:md:w-3/5', 'data-[side=right]:md:max-w-6xl')
    expect(history).toHaveClass('h-[clamp(18rem,45vh,36rem)]', 'overflow-y-auto')
  })

  it('removes event handlers, styles, unsafe URLs, SVG, and MathML from history', async () => {
    const { container } = renderDrawer(
      '<div style="color:red" onclick="alert(1)">safe</div>\n\n[x](javascript:alert(1))\n\n<svg onload="alert(1)"></svg><math><mi>x</mi></math>',
    )

    await screen.findByRole('dialog')
    expect(container.querySelector('[onclick], [style], svg, math')).toBeNull()
    expect(document.querySelector('a')).not.toHaveAttribute('href')
  })

  it('closes on Escape and restores focus to the opener', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [open, setOpen] = useState(false)
      return <div>
        <button type="button" onClick={() => setOpen(true)}>Originating row</button>
        <ReviewDrawer
          open={open}
          activeEntry={anEntry()}
          detailStatus="ready"
          detailProblem={null}
          mutationPending={false}
          saveProblem={null}
          synthesisPhase={null}
          synthesisPending={false}
          synthesisProblem={null}
          onClose={() => setOpen(false)}
          onSave={() => {}}
          onSynthesize={() => {}}
          onRollback={() => {}}
        />
      </div>
    }
    render(<Harness />)

    const opener = screen.getByRole('button', { name: 'Originating row' })
    await user.click(opener)

    await screen.findByRole('dialog')
    await user.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(opener).toHaveFocus())
  })
})
