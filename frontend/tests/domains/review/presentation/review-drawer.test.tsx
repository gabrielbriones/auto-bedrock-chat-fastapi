import { describe, expect, it } from '@jest/globals'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ReviewDrawer } from '@/domains/review/presentation/ReviewDrawer'
import { REVIEW_COPY } from '@/shared/copy/review'
import type { FeedbackEntry } from '@/domains/review/domain/public'

import { anEntry } from '../domain/feedback-entry.fixture'

const renderDrawer = (aiResponse: string, overrides: Partial<FeedbackEntry> = {}) =>
  render(
    <ReviewDrawer
      open
      activeEntry={anEntry({ aiResponse, ...overrides })}
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

  it('keeps the decision before history in reading order and does not duplicate the final response', async () => {
    renderDrawer('Final answer', {
      conversationHistory: [{ role: 'user', content: 'Question' }, { role: 'assistant', content: 'Final answer' }],
    })

    await screen.findByRole('dialog', { name: REVIEW_COPY.drawer.title })
    expect(screen.getByText('Question')).toBeInTheDocument()
    expect(screen.getAllByText('Final answer')).toHaveLength(1)
    expect(screen.queryByText(REVIEW_COPY.drawer.response)).not.toBeInTheDocument()
    const decision = screen.getByRole('group', { name: REVIEW_COPY.form.decision })
    const history = screen.getByText(REVIEW_COPY.drawer.history)
    expect(decision.compareDocumentPosition(history) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('shows metadata before the initially open, collapsible message history', async () => {
    renderDrawer('Final answer', { entryMetadata: { job_id: '9001' } })

    await screen.findByRole('dialog', { name: REVIEW_COPY.drawer.title })
    const metadata = screen.getByText(REVIEW_COPY.drawer.metadata)
    const summary = screen.getByText(REVIEW_COPY.drawer.history)
    const history = summary.closest('details')
    expect(metadata.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(history).toHaveAttribute('open')
    await userEvent.setup().click(summary)
    expect(history).not.toHaveAttribute('open')
  })
})
