import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import {
  ThemeContext,
  type ResolvedTheme,
  type ThemePreference,
  type ThemeState,
} from '@/app/providers/theme-context'
import { THEME_STORAGE_KEY } from '@/app/theme/theme-bootstrap'

const DARK_QUERY = '(prefers-color-scheme: dark)'

// Storage is unavailable in some privacy modes; a theme is never worth breaking the app over.
const readStoredPreference = (): ThemePreference => {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)

    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
  } catch {
    return 'system'
  }
}

const storePreference = (preference: ThemePreference): void => {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, preference)
  } catch {
    // Preference simply does not survive the session.
  }
}

const systemTheme = (): ResolvedTheme =>
  window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'

export type ThemeProviderProps = {
  readonly children: ReactNode
}

// FR-DS-002/003. The inline bootstrap in index.html has already applied the first frame's class;
// this keeps it in step afterwards, including live OS changes while the preference is `system`.
export function ThemeProvider({ children }: ThemeProviderProps) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference)
  const [systemResolved, setSystemResolved] = useState<ResolvedTheme>(systemTheme)

  useEffect(() => {
    const media = window.matchMedia(DARK_QUERY)
    const onChange = (event: MediaQueryListEvent) => {
      setSystemResolved(event.matches ? 'dark' : 'light')
    }

    media.addEventListener('change', onChange)

    return () => {
      media.removeEventListener('change', onChange)
    }
  }, [])

  const resolved: ResolvedTheme = preference === 'system' ? systemResolved : preference

  useEffect(() => {
    const root = document.documentElement

    root.classList.remove('light', 'dark')
    root.classList.add(resolved)
  }, [resolved])

  const setPreference = useCallback((next: ThemePreference) => {
    storePreference(next)
    setPreferenceState(next)
  }, [])

  const value = useMemo<ThemeState>(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
