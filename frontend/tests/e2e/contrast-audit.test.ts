import { expect } from '@jest/globals'
import { By, type WebDriver } from 'selenium-webdriver'

import {
  startAccessibilityServer,
  type AccessibilityServer,
} from './helpers/accessibility-server.js'
import { BOUNDARY_CONTRAST_PAIRS, measureContrastRatios, TEXT_CONTRAST_PAIRS } from './helpers/contrast.js'
import { buildChromeDriver } from './helpers/webdriver.js'

type Theme = 'light' | 'dark'

// NFR-A11Y-004: every semantic token pair meets its contrast target in both themes, checked
// against the browser's own resolved colors so color-mix()/oklch ramps are measured for real.
describe('Phase 10 contrast audit', () => {

  let driver: WebDriver
  let server: AccessibilityServer

  beforeAll(async () => {
    server = await startAccessibilityServer()
    driver = await buildChromeDriver()
  })

  afterAll(async () => {
    await driver?.quit()
    await server?.close()
  })

  for (const theme of ['light', 'dark'] as const satisfies readonly Theme[]) {
    describe(`${theme} theme`, () => {
      beforeAll(async () => {
        await driver.get(`${server.origin}/bedrock-chat/ui/`)
        await driver.executeScript("localStorage.setItem('ui.theme', arguments[0])", theme)
        await driver.get(`${server.origin}/bedrock-chat/ui/`)
        await driver.wait(
          async () => ((await driver.findElement(By.css('html')).getAttribute('class')) ?? '').split(/\s+/).includes(theme),
          10_000,
        )
      })

      it('meets 4.5:1 for text token pairs', async () => {
        const ratios = await measureContrastRatios(driver, TEXT_CONTRAST_PAIRS)
        const failures = TEXT_CONTRAST_PAIRS.filter((pair) => (ratios[pair.name] ?? 0) < pair.minRatio).map(
          (pair) => `${pair.name}: ${(ratios[pair.name] ?? 0).toFixed(2)}:1 (needs >= ${pair.minRatio}:1)`,
        )
        expect(failures).toEqual([])
      })

      it('meets 3:1 for UI boundary token pairs', async () => {
        const ratios = await measureContrastRatios(driver, BOUNDARY_CONTRAST_PAIRS)
        const failures = BOUNDARY_CONTRAST_PAIRS.filter((pair) => (ratios[pair.name] ?? 0) < pair.minRatio).map(
          (pair) => `${pair.name}: ${(ratios[pair.name] ?? 0).toFixed(2)}:1 (needs >= ${pair.minRatio}:1)`,
        )
        expect(failures).toEqual([])
      })
    })
  }
})
