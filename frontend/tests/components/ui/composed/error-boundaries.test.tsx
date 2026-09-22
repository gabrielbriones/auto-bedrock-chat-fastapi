import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AppShell } from '@/app/layouts/AppShell'
import { OverlayBoundary, RouteBoundary } from '@/components/ui/composed/error-boundaries'
import { SHELL } from '@/shared/copy/shell'

function Thrower(): never {
  throw new Error('boom')
}

// React logs a caught render error to console.error; the boundary is what the test is about.
const silenceReactErrorLog = () => vi.spyOn(console, 'error').mockImplementation(() => {})

describe('error boundaries', () => {
  it('keeps the shell alive when a route body throws', () => {
    silenceReactErrorLog()

    render(
      <AppShell header={<header>Chat header</header>}>
        <RouteBoundary resetKey="/">
          <Thrower />
        </RouteBoundary>
      </AppShell>,
    )

    expect(screen.getByText('Chat header')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(SHELL.error.routeDescription)
  })

  it('takes down only the overlay when an overlay throws', () => {
    silenceReactErrorLog()

    render(
      <AppShell
        overlays={
          <OverlayBoundary resetKey="drawer">
            <Thrower />
          </OverlayBoundary>
        }
      >
        <p>Transcript</p>
      </AppShell>,
    )

    expect(screen.getByText('Transcript')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(SHELL.error.overlayDescription)
  })

  it('reports the failure once to the observability sink', () => {
    silenceReactErrorLog()
    const onCatch = vi.fn()

    render(
      <RouteBoundary resetKey="/" onCatch={onCatch}>
        <Thrower />
      </RouteBoundary>,
    )

    expect(onCatch).toHaveBeenCalledTimes(1)
  })

  it('offers a retry that re-renders the failed subtree', async () => {
    silenceReactErrorLog()
    const user = userEvent.setup()
    let shouldThrow = true

    function Flaky() {
      if (shouldThrow) {
        throw new Error('boom')
      }

      return <p>Recovered</p>
    }

    render(
      <RouteBoundary resetKey="/">
        <Flaky />
      </RouteBoundary>,
    )

    shouldThrow = false
    await user.click(screen.getByRole('button', { name: SHELL.error.retry }))

    expect(screen.getByText('Recovered')).toBeInTheDocument()
  })
})
