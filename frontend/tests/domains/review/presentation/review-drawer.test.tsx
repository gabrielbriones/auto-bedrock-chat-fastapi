import { describe, expect, it } from '@jest/globals'
import { render, screen } from '@testing-library/react'

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
})
