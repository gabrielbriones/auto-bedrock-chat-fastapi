import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ReviewForm } from '@/domains/review/presentation/ReviewForm'
import { REVIEW_COPY } from '@/shared/copy/review'

import { anEntry } from '../domain/feedback-entry.fixture'

describe('ReviewForm', () => {
  it('requires a decision before sending a request', async () => {
    const onSave = vi.fn()
    const user = userEvent.setup()
    render(<ReviewForm entry={anEntry()} pending={false} problem={null} onSave={onSave} />)

    await user.click(screen.getByRole('button', { name: REVIEW_COPY.form.save }))

    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(REVIEW_COPY.form.requiredDecision)
    expect(screen.getByRole('group', { name: REVIEW_COPY.form.decision })).toHaveAccessibleDescription(
      REVIEW_COPY.form.requiredDecision,
    )
  })

  it('submits cleared tags and comment as explicit empty values', async () => {
    const onSave = vi.fn()
    const user = userEvent.setup()
    const entry = anEntry({
      reviewStatus: 'approved',
      reviewerTags: ['wrong-tag'],
      review: { reviewerId: 'reviewer', reviewedAt: null, tags: ['wrong-tag'], comment: 'wrong comment' },
    })
    render(<ReviewForm entry={entry} pending={false} problem={null} onSave={onSave} />)

    await user.click(screen.getByRole('button', { name: REVIEW_COPY.tags.remove('wrong-tag') }))
    await user.clear(screen.getByRole('textbox', { name: REVIEW_COPY.form.comment }))
    await user.click(screen.getByRole('button', { name: REVIEW_COPY.form.save }))

    expect(onSave).toHaveBeenCalledWith({ decision: 'approved', tags: [], comment: null })
  })

  it('maps a 409 to the stated conflict message', () => {
    render(
      <ReviewForm
        entry={anEntry({ reviewStatus: 'approved' })}
        pending={false}
        problem={{ code: 'http-error', title: 'Conflict', status: 409 }}
        onSave={() => {}}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent(REVIEW_COPY.form.conflict)
  })

  it('maps a 422 detail to the stated validation message', () => {
    render(
      <ReviewForm
        entry={anEntry({ reviewStatus: 'approved' })}
        pending={false}
        problem={{ code: 'http-error', title: 'Invalid', status: 422, detail: 'reviewer_tags is invalid' }}
        onSave={() => {}}
      />,
    )

    const message = 'Validation error: reviewer_tags is invalid'
    expect(screen.getByRole('alert')).toHaveTextContent(message)
    expect(screen.getByRole('textbox', { name: REVIEW_COPY.form.tags })).toHaveAccessibleDescription(message)
  })
})