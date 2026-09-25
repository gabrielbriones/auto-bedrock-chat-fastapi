import '@testing-library/jest-dom/jest-globals'
import type { TestingLibraryMatchers } from '@testing-library/jest-dom/matchers'
import { cleanup } from '@testing-library/react'
import { afterEach, expect } from '@jest/globals'

// jest-dom's own `jest-globals` typing augments `@jest/expect`, but Jest 30 defines `Matchers` in
// the `expect` package, so the DOM matchers are declared against that module here instead.
declare module 'expect' {
  interface Matchers<R extends void | Promise<void>, T = unknown>
    extends TestingLibraryMatchers<ReturnType<typeof expect.stringContaining>, R> {}
}

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

// jsdom defines `scrollTo` only to log "Not implemented"; the router's scroll restoration calls
// it on every navigation. There is no layout to scroll, so a silent no-op is the honest stub.
window.scrollTo = () => {}

// Testing Library auto-unmounts only when it detects a global `afterEach` at import time; kept
// explicit so the guarantee does not hinge on import order.
afterEach(() => {
  cleanup()
})
