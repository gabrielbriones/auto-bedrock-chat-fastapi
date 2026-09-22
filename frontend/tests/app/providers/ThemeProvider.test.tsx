import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ThemeProvider } from '@/app/providers/ThemeProvider'
import { useTheme } from '@/app/providers/theme-context'
import { THEME_STORAGE_KEY } from '@/app/theme/theme-bootstrap'

type MediaListener = (event: MediaQueryListEvent) => void

const listeners = new Set<MediaListener>()
let prefersDark = false

const installMatchMedia = () => {
  listeners.clear()
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      get matches() {
        return prefersDark
      },
      media: query,
      addEventListener: (_: string, listener: MediaListener) => {
        listeners.add(listener)
      },
      removeEventListener: (_: string, listener: MediaListener) => {
        listeners.delete(listener)
      },
    }) as unknown as MediaQueryList
}

const changeSystemPreference = (dark: boolean) => {
  prefersDark = dark

  for (const listener of listeners) {
    listener({ matches: dark } as MediaQueryListEvent)
  }
}

function ThemeProbe() {
  const { preference, resolved, setPreference } = useTheme()

  return (
    <div>
      <p>{`${preference}/${resolved}`}</p>
      <button type="button" onClick={() => setPreference('light')}>
        Light
      </button>
    </div>
  )
}

const renderProvider = () =>
  render(
    <ThemeProvider>
      <ThemeProbe />
    </ThemeProvider>,
  )

beforeEach(() => {
  localStorage.clear()
  prefersDark = false
  installMatchMedia()
  document.documentElement.classList.remove('light', 'dark')
})

describe('ThemeProvider', () => {
  it('applies a stored explicit preference', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')

    renderProvider()

    expect(screen.getByText('dark/dark')).toBeInTheDocument()
    expect(document.documentElement).toHaveClass('dark')
  })

  it('follows the system preference when none is stored', () => {
    prefersDark = true

    renderProvider()

    expect(screen.getByText('system/dark')).toBeInTheDocument()
    expect(document.documentElement).toHaveClass('dark')
  })

  it('tracks a live system change without a reload', async () => {
    renderProvider()

    expect(document.documentElement).toHaveClass('light')

    await Promise.resolve(changeSystemPreference(true))

    expect(await screen.findByText('system/dark')).toBeInTheDocument()
    // The class-sync effect commits in the same act flush as the text update, but under load
    // (e.g. the full suite) that flush can still be one tick behind — wait for it too.
    await waitFor(() => expect(document.documentElement).toHaveClass('dark'))
  })

  it('persists an explicit choice and stops following the system', async () => {
    prefersDark = true
    renderProvider()

    await userEvent.click(screen.getByRole('button', { name: 'Light' }))

    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(screen.getByText('light/light')).toBeInTheDocument()
    expect(document.documentElement).toHaveClass('light')
  })

  it('never carries both theme classes at once', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')

    renderProvider()

    expect(document.documentElement).not.toHaveClass('light')
  })
})
