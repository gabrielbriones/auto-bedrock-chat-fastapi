import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from '@jest/globals'
import { axe } from 'jest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { fakeContainer } from '../../../app/bootstrap/container.fixture'
import { messageId } from '@/shared/kernel/branded'

import type {
  FeedbackGateway,
  FeedbackGatewayError,
  SubmittedFeedbackLog,
} from '@/domains/feedback/application/ports'
import { FeedbackStore } from '@/domains/feedback/application/feedback.store'
import { FeedbackControls } from '@/domains/feedback/presentation/FeedbackControls'

const id = messageId('m-1')

const createHarness = (sendResult: 'sent' | 'dropped-closed' = 'sent') => {
  const submitted = new Set<typeof id>()
  let emitError: ((error: FeedbackGatewayError) => void) | undefined
  const sent: Record<string, unknown>[] = []
  const gateway: FeedbackGateway = {
    submit(payload) {
      sent.push(payload)
      return sendResult
    },
    onAck: () => () => {},
    onError(listener) {
      emitError = listener
      return () => {}
    },
  }
  const log: SubmittedFeedbackLog = {
    all: () => submitted,
    mark: (value) => submitted.add(value),
    unmark: (value) => submitted.delete(value),
  }
  const store = new FeedbackStore({ gateway, submittedLog: log })

  return {
    sent,
    store,
    error(error: FeedbackGatewayError) {
      emitError?.(error)
    },
  }
}

const renderControls = (store: FeedbackStore) =>
  render(
    <ContainerContext.Provider value={fakeContainer({ feedback: store })}>
      <FeedbackControls messageId={id} />
    </ContainerContext.Provider>,
  )

describe('FeedbackControls', () => {
  it('exposes rating state and focuses the first correction field', async () => {
    const user = userEvent.setup()
    const harness = createHarness()
    renderControls(harness.store)

    const positive = await waitFor(() => screen.getByRole('button', { name: 'Rate response helpful' }))
    const negative = screen.getByRole('button', { name: 'Rate response unhelpful' })
    expect(positive).toHaveAttribute('aria-pressed', 'false')
    expect(negative).toHaveAttribute('aria-pressed', 'false')
    expect(negative).toHaveAttribute('aria-expanded', 'false')

    await user.click(negative)

    // The unhelpful button now toggles the correction popover, so it stays clickable while open.
    expect(negative).toBeEnabled()
    expect(positive).toBeDisabled()
    expect(negative).toHaveAttribute('aria-expanded', 'true')
    expect(negative).toHaveAttribute('aria-haspopup', 'dialog')
    expect(screen.getByRole('textbox', { name: 'What should the correct answer be? (optional)' })).toHaveFocus()
  })

  it('cancels and discards a correction draft', async () => {
    const user = userEvent.setup()
    const harness = createHarness()
    renderControls(harness.store)

    await user.click(screen.getByRole('button', { name: 'Rate response unhelpful' }))
    const correction = screen.getByRole('textbox', { name: 'What should the correct answer be? (optional)' })
    await user.type(correction, 'temporary correction')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(screen.getByRole('button', { name: 'Rate response unhelpful' }))

    expect(screen.getByRole('textbox', { name: 'What should the correct answer be? (optional)' })).toHaveValue('')
  })

  it('omits blank negative fields and replaces controls with a status region', async () => {
    const user = userEvent.setup()
    const harness = createHarness()
    renderControls(harness.store)

    await user.click(screen.getByRole('button', { name: 'Rate response unhelpful' }))
    await user.click(screen.getByRole('button', { name: 'Submit Feedback' }))

    expect(harness.sent).toEqual([{ message_id: 'm-1', rating: 'negative' }])
    expect(screen.getByRole('status')).toHaveTextContent('✓ Feedback submitted')
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite')
  })

  it('restores controls and announces a mapped server error', async () => {
    const user = userEvent.setup()
    const harness = createHarness()
    renderControls(harness.store)

    await user.click(screen.getByRole('button', { name: 'Rate response helpful' }))
    harness.error({
      code: 'unauthorized_feedback',
      message: 'server detail',
      messageId: id,
    })

    const positive = await waitFor(() => screen.getByRole('button', { name: 'Rate response helpful' }))
    expect(positive).toBeEnabled()
    expect(screen.getByRole('alert')).toHaveTextContent('You are not allowed to rate this message.')
    expect(positive).toHaveAttribute('aria-describedby', 'feedback-error-m-1')
  })

  it('has no axe violations', async () => {
    const harness = createHarness()
    const { container } = renderControls(harness.store)

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})