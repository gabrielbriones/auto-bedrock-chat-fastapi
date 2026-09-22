import { createContext, useContext } from 'react'

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export type ThemeState = {
  readonly preference: ThemePreference
  readonly resolved: ResolvedTheme
  readonly setPreference: (preference: ThemePreference) => void
}

export const ThemeContext = createContext<ThemeState | undefined>(undefined)

export const useTheme = (): ThemeState => {
  const theme = useContext(ThemeContext)

  if (theme === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }

  return theme
}
