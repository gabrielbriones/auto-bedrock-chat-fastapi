import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// jsdom has no matchMedia, and the theme provider (FR-DS-002) needs one. Tests that care about
// the system preference replace this with their own controllable stub.
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

// jsdom computes no layout and so ships no ResizeObserver; @tanstack/react-virtual constructs one
// unconditionally to measure rows. A no-op is honest here: with zero-height boxes there is nothing
// to report anyway, and tests that care about sizes stub the metrics directly.
if (typeof globalThis.ResizeObserver !== 'function') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
}

// Testing Library doesn't auto-unmount between tests outside of Jest; without this every
// component test after the first would render into a DOM still holding the previous test's tree.
afterEach(() => {
  cleanup()
})
