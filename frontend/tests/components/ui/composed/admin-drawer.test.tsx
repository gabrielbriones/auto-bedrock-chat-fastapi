import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AdminDrawer } from '@/components/ui/composed/admin-drawer'

function Harness() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true)
        }}
      >
        Open review
      </button>
      <AdminDrawer open={open} onOpenChange={setOpen} title="Review entry" description="Feedback details">
        <button type="button">Approve</button>
        <button type="button">Reject</button>
      </AdminDrawer>
    </>
  )
}

const trigger = () => screen.getByRole('button', { name: 'Open review' })

// FIX-13. The legacy drawer was a positioned `<div>`: Tab walked out of it, Escape did nothing,
// and closing it dropped focus onto `<body>`. All three are asserted here so the primitive every
// admin drawer is built on cannot regress them.
describe('AdminDrawer', () => {
  it('names itself and its purpose', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(trigger())

    const dialog = await screen.findByRole('dialog', { name: 'Review entry' })
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText('Feedback details')).toBeInTheDocument()
  })

  it('traps focus inside the drawer, leaving the page behind unreachable', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(trigger())
    const dialog = await screen.findByRole('dialog')

    // The trigger is still in the DOM but marked inert, so neither Tab nor a screen reader can
    // reach the page behind the drawer.
    expect(screen.queryByRole('button', { name: 'Open review' })).not.toBeInTheDocument()
    expect(dialog).toContainElement(screen.getByRole('button', { name: 'Approve' }))

    await user.tab()
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
  })

  it('closes on Escape and restores focus to the control that opened it', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(trigger())
    await screen.findByRole('dialog')

    await user.keyboard('{Escape}')

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    await waitFor(() => {
      expect(trigger()).toHaveFocus()
    })
  })
})
