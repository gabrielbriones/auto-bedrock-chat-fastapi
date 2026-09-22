import { AxeBuilder } from '@axe-core/webdriverjs'
import type { WebDriver } from 'selenium-webdriver'

export type AxeScanOptions = {
  /** Narrows the scan to one region, for journeys that own a region rather than the whole page. */
  readonly include?: string
}

// STD-002 §1: every E2E journey also runs an axe scan (NFR-A11Y-004).
export async function assertNoAxeViolations(
  driver: WebDriver,
  options: AxeScanOptions = {},
): Promise<void> {
  const builder = new AxeBuilder(driver)
  const results = await (options.include === undefined
    ? builder
    : builder.include(options.include)
  ).analyze()

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
