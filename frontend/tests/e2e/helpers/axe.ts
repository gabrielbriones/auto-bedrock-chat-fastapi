import { AxeBuilder } from '@axe-core/webdriverjs'
import type { WebDriver } from 'selenium-webdriver'

import { NARRATOR_SELECTOR, withNarratorHidden } from './webdriver.js'

export type AxeScanOptions = {
  /** Narrows the scan to one region, for journeys that own a region rather than the whole page. */
  readonly include?: string
  /** axe rule ids to leave out, for a scan whose environment makes a rule meaningless (say why). */
  readonly disableRules?: readonly string[]
}

// STD-002 §1: every E2E journey also runs an axe scan (NFR-A11Y-004).
export async function assertNoAxeViolations(
  driver: WebDriver,
  options: AxeScanOptions = {},
): Promise<void> {
  // The watch-mode narration banner is test scaffolding, not part of the page under audit.
  const builder = new AxeBuilder(driver)
    .exclude(NARRATOR_SELECTOR)
    .disableRules([...(options.disableRules ?? [])])
  const scan = options.include === undefined ? builder : builder.include(options.include)
  const results = await withNarratorHidden(driver, () => scan.analyze())

  if (results.violations.length > 0) {
    const summary = results.violations
      .map((violation) => {
        const targets = violation.nodes.flatMap((node) => node.target).join(', ')
        return `${violation.id}: ${violation.description} (${targets})`
      })
      .join('\n')
    throw new Error(`axe found ${results.violations.length} violation(s):\n${summary}`)
  }
}
