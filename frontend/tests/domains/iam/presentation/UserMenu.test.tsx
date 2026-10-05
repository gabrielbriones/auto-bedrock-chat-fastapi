import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from '@jest/globals'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { ThemeProvider } from '@/app/providers/ThemeProvider'
import { UserMenu } from '@/domains/iam/presentation/UserMenu'

import { fakeContainer } from '../../../app/bootstrap/container.fixture'

describe('UserMenu', () => {
  it('opens the account menu without crashing', async () => {
    const container = fakeContainer()
    const user = userEvent.setup({ pointerEventsCheck: 0 })

    render(
      <ContainerContext.Provider value={container}>
        <ThemeProvider>
          <UserMenu />
        </ThemeProvider>
      </ContainerContext.Provider>,
    )

    act(() => {
      container.messageBus.receive(
        JSON.stringify({
          type: 'auth_configured',
          timestamp: '2026-08-28T08:59:00Z',
          message: 'Authenticated',
          auth_type: 'api_key',
          display_name: 'Test user',
        }),
      )
    })

    await user.click(await screen.findByRole('button', { name: 'Test user' }))

    expect(await screen.findByRole('menuitemradio', { name: 'Dark' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Log out' })).toBeInTheDocument()
  })
})
