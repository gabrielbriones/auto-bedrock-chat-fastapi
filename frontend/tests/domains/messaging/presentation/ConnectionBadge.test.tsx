import { render, screen } from '@testing-library/react'
import { describe, expect, it } from '@jest/globals'
import { axe } from 'jest-axe'

import { ConnectionBadge } from '@/domains/messaging/presentation/ConnectionBadge'

describe('ConnectionBadge (FR-MSG-002)', () => {
  it.each(['connected', 'connecting'] as const)('renders nothing while %s', (kind) => {
    const { container } = render(<ConnectionBadge connection={{ kind }} />)

    expect(container).toBeEmptyDOMElement()
  })

  it.each([[{ kind: 'disconnected' } as const, 'Disconnected']])(
    'renders %o as its label',
    (connection, label) => {
      render(<ConnectionBadge connection={connection} />)

      expect(screen.getByRole('alert', { name: 'Connection status' })).toHaveTextContent(label)
    },
  )

  it('shows the disconnected banner immediately on connection loss, with no intermediate reconnecting state', () => {
    const { rerender } = render(<ConnectionBadge connection={{ kind: 'connected' }} />)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    rerender(<ConnectionBadge connection={{ kind: 'disconnected' }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Disconnected')

    rerender(<ConnectionBadge connection={{ kind: 'connected' }} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  // NFR-A11Y-001
  it('has no axe violations', async () => {
    const { container } = render(<ConnectionBadge connection={{ kind: 'disconnected' }} />)

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
