// FR-TOOL-019: the one module that reads (and, for the prompt-catalog deep link, writes) the
// address bar directly. Everything else navigates through the router's typed search state; the
// exceptions below run outside a route context: the startup path (FR-IAM-007's `?auth_method=`),
// the SSO round trip (FR-IAM-005), and the prompt-catalog deep link (FR-PROMPT-008/009), which is
// parsed and scrubbed before the composition root even builds the router.

import type { UrlNavigator } from '@/domains/prompt-catalog/application/ports'

/** The current location as an application-relative URL, for round-tripping back to it. */
export const currentPath = (): string => window.location.pathname + window.location.search

/** Reads one query parameter of the current location. */
export const locationParam = (name: string): string | null =>
  new URLSearchParams(window.location.search).get(name)

// FR-PROMPT-009: `replace` never pushes a new history entry — a scrub must not be a page a
// back-navigation can return to.
export const browserUrlNavigator: UrlNavigator = {
  current: () => Object.fromEntries(new URLSearchParams(window.location.search).entries()),
  replace: (next) => {
    const query = new URLSearchParams(next).toString()
    const url = `${window.location.pathname}${query === '' ? '' : `?${query}`}${window.location.hash}`
    window.history.replaceState(window.history.state, '', url)
  },
}
