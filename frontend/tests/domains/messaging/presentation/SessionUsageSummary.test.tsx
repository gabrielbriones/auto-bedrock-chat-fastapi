import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from '@jest/globals'
import { axe } from 'jest-axe'

import type { SessionUsage } from '@/domains/messaging/domain/session-usage'
import { SessionUsageSummary } from '@/domains/messaging/presentation/SessionUsageSummary'
import { createTokenCount } from '@/domains/telemetry/domain/public'

const session = (overrides: Partial<SessionUsage> = {}): SessionUsage => ({
  tokens: createTokenCount(150_000, 12_000, 90_000, 4_000),
  estimatedCost: 0.3124,
  currency: 'USD',
  unpricedAnswers: 0,
  answers: 4,
  pending: false,
  context: { usedTokens: 126_000, maxTokens: 1_000_000 },
  ...overrides,
})

const trigger = () => screen.getByRole('button', { name: /^Conversation usage/ })

describe('SessionUsageSummary', () => {
  it('renders nothing before any answer reported usage', () => {
    const { container } = render(<SessionUsageSummary usage={null} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('shows the context fill and the conversation estimate', () => {
    render(<SessionUsageSummary usage={session()} />)

    expect(trigger()).toHaveTextContent('13% context$0.3124')
  })

  it('falls back to the token total without an estimate, and hides context when the window is unknown', () => {
    render(<SessionUsageSummary usage={session({ estimatedCost: null, currency: null, context: null })} />)

    expect(trigger()).toHaveTextContent('162k tokens')
    expect(trigger()).not.toHaveTextContent('context')
    expect(trigger()).not.toHaveTextContent('$')
  })

  it('details totals, the context meter and unpriced answers on hover', async () => {
    const user = userEvent.setup()
    render(<SessionUsageSummary usage={session({ unpricedAnswers: 1, pending: true })} />)

    await user.hover(trigger())

    const card = await screen.findByRole('dialog')
    expect(card).toHaveTextContent('Estimated $0.3124')
    expect(card).toHaveTextContent('Total150,000 in / 12,000 out')
    expect(card).toHaveTextContent('Answers4')
    expect(card).toHaveTextContent('126,000 of 1,000,000 tokens')
    expect(card).toHaveTextContent('1 answer has no price and is not included')
    expect(card).toHaveTextContent('Final total pending')
    expect(screen.getByRole('meter', { name: 'Context window used' })).toHaveAttribute('aria-valuenow', '13')
  })

  it('gives screen readers the summary in the trigger name', () => {
    render(<SessionUsageSummary usage={session()} />)

    expect(trigger()).toHaveAccessibleName(
      'Conversation usage. Estimated $0.3124. 150,000 input tokens, 12,000 output tokens. ' +
        'Context window: 126,000 of 1,000,000 tokens.',
    )
  })

  it('has no axe violations with the card open', async () => {
    const user = userEvent.setup()
    const { container } = render(<SessionUsageSummary usage={session()} />)
    await user.hover(trigger())
    await screen.findByRole('dialog')

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
