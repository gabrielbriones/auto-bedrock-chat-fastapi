import { describe, expect, it } from '@jest/globals'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ConfirmationHost } from '@/app/providers/ConfirmationHost'
import { ConfirmationController } from '@/app/adapters/confirmation-controller'
import { SHELL } from '@/shared/copy/shell'

const renderHost = () => {
  const controller = new ConfirmationController()

  render(<ConfirmationHost controller={controller} />)

  return { controller, user: userEvent.setup() }
}

describe('the confirmation host', () => {
  it('resolves true when the confirm action is taken', async () => {
    const { controller, user } = renderHost()
    const answer = controller.confirm({ title: 'Delete entry?', message: 'This is permanent.' })

    await user.click(await screen.findByRole('button', { name: SHELL.confirmations.confirm }))

    await expect(answer).resolves.toBe(true)
  })

  // FR-SHELL-008: the destructive action is never what the dialog opens focused on.
  it('opens with focus on cancel, not on the destructive action', async () => {
    const { controller } = renderHost()
    void controller.confirm({
      title: 'Delete entry?',
      message: 'This is permanent.',
      tone: 'destructive',
    })

    const cancel = await screen.findByRole('button', { name: SHELL.confirmations.cancel })

    await waitFor(() => {
      expect(cancel).toHaveFocus()
    })
  })

  it('resolves false when dismissed with Escape', async () => {
    const { controller, user } = renderHost()
    const answer = controller.confirm({ title: 'Delete entry?', message: '' })

    await screen.findByRole('button', { name: SHELL.confirmations.confirm })
    await user.keyboard('{Escape}')

    await expect(answer).resolves.toBe(false)
  })

  it('returns the typed value from a prompt', async () => {
    const { controller, user } = renderHost()
    const answer = controller.prompt({ title: 'Rollback', label: 'Reason' })

    await user.type(await screen.findByLabelText('Reason'), 'wrong answer')
    await user.click(screen.getByRole('button', { name: SHELL.confirmations.submit }))

    await expect(answer).resolves.toBe('wrong answer')
  })

  it('accepts an empty submission only when the prompt is optional', async () => {
    const { controller, user } = renderHost()
    const required = controller.prompt({ title: 'Rollback', label: 'Reason' })

    expect(await screen.findByRole('button', { name: SHELL.confirmations.submit })).toBeDisabled()

    await user.keyboard('{Escape}')
    await expect(required).resolves.toBeNull()

    const optional = controller.prompt({
      title: 'Rollback',
      label: 'Optional reason',
      optional: true,
    })

    await screen.findByLabelText('Optional reason')
    await user.click(screen.getByRole('button', { name: SHELL.confirmations.submit }))

    await expect(optional).resolves.toBe('')
  })

  // FR-SHELL-020 / FR-SHELL-005: concurrent requests are answered newest first and settle apart.
  it('keeps two open confirmations independent, answering the newest first', async () => {
    const { controller, user } = renderHost()
    const first = controller.confirm({ title: 'First', message: '' })
    const second = controller.confirm({ title: 'Second', message: '' })

    await screen.findByText('Second')
    await user.click(screen.getByRole('button', { name: SHELL.confirmations.confirm }))
    await expect(second).resolves.toBe(true)

    await screen.findByText('First')
    await user.click(screen.getByRole('button', { name: SHELL.confirmations.cancel }))
    await expect(first).resolves.toBe(false)
  })
})
