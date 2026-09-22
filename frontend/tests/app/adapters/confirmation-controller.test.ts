import { describe, expect, it } from 'vitest'

import { ConfirmationController } from '@/app/adapters/confirmation-controller'

describe('ConfirmationController', () => {
  it('holds the request open until it is settled', async () => {
    const controller = new ConfirmationController()
    const answer = controller.confirm({ title: 'Delete?', message: 'This cannot be undone.' })

    const [pending] = controller.getSnapshot()
    expect(pending?.request.title).toBe('Delete?')

    controller.settle(pending?.id ?? '', true)

    await expect(answer).resolves.toBe(true)
    expect(controller.getSnapshot()).toHaveLength(0)
  })

  // FR-SHELL-020: the legacy single-resolver design orphaned the first promise.
  it('resolves overlapping confirmations independently', async () => {
    const controller = new ConfirmationController()

    const first = controller.confirm({ title: 'First', message: '' })
    const second = controller.confirm({ title: 'Second', message: '' })

    const [one, two] = controller.getSnapshot()
    controller.settle(two?.id ?? '', true)
    controller.settle(one?.id ?? '', false)

    await expect(first).resolves.toBe(false)
    await expect(second).resolves.toBe(true)
  })

  it('resolves a cancelled prompt with null rather than rejecting', async () => {
    const controller = new ConfirmationController()
    const answer = controller.prompt({ title: 'Reason', label: 'Reason' })

    controller.settle(controller.getSnapshot()[0]?.id ?? '', null)

    await expect(answer).resolves.toBeNull()
  })

  it('ignores a second settlement of the same request', async () => {
    const controller = new ConfirmationController()
    const answer = controller.confirm({ title: 'Delete?', message: '' })
    const id = controller.getSnapshot()[0]?.id ?? ''

    controller.settle(id, true)
    controller.settle(id, false)

    await expect(answer).resolves.toBe(true)
  })

  it('notifies subscribers when the pending set changes', () => {
    const controller = new ConfirmationController()
    let notifications = 0
    const unsubscribe = controller.subscribe(() => {
      notifications += 1
    })

    void controller.confirm({ title: 'Delete?', message: '' })
    controller.settle(controller.getSnapshot()[0]?.id ?? '', false)
    unsubscribe()
    void controller.confirm({ title: 'Ignored', message: '' })

    expect(notifications).toBe(2)
  })
})
