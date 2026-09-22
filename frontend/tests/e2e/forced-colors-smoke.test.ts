import { expect } from 'chai'
import { By, Key, until, type WebDriver, type WebElement } from 'selenium-webdriver'

import { assertNoAxeViolations } from './helpers/axe.js'
import {
  startAccessibilityServer,
  type AccessibilityServer,
} from './helpers/accessibility-server.js'
import { enableForcedColors } from './helpers/forced-colors.js'
import { buildChromeDriver } from './helpers/webdriver.js'

const activeElementMatches = (driver: WebDriver, selector: string) =>
  driver.executeScript<boolean>(
    'return document.activeElement instanceof HTMLElement && document.activeElement.matches(arguments[0])',
    selector,
  )

const tabTo = async (driver: WebDriver, selector: string): Promise<WebElement> => {
  await driver.executeScript('if (document.activeElement instanceof HTMLElement) document.activeElement.blur()')

  for (let tab = 0; tab < 40; tab += 1) {
    await driver.actions().sendKeys(Key.TAB).perform()
    if (await activeElementMatches(driver, selector)) {
      const element = await driver.switchTo().activeElement()
      // The visible focus indicator may live on the element itself (outline/box-shadow) or on a
      // `focus-within` ancestor wrapper (e.g. the chat composer's bordered container) — check both.
      const hasVisibleFocus = await driver.executeScript<boolean>(
        `const hasIndicator = (el) => {
           const style = getComputedStyle(el);
           return (style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0) ||
             style.boxShadow !== 'none';
         };
         const element = document.activeElement;
         if (!element.matches(':focus-visible')) return false;
         let node = element;
         for (let depth = 0; depth < 4 && node; depth += 1) {
           if (hasIndicator(node)) return true;
           node = node.parentElement;
         }
         return false;`,
      )
      expect(hasVisibleFocus, `${selector} must stay visibly focusable in forced-colors mode`).to.equal(true)
      return element
    }
  }

  throw new Error(`keyboard traversal did not reach ${selector} in forced-colors mode`)
}

// Every element matching `selector` must still occupy a real box and be paintable: forced-colors
// mode strips author colors, but it must not collapse layout or hide content (NFR-A11Y-007).
const assertVisible = async (driver: WebDriver, selector: string): Promise<void> => {
  const elements = await driver.findElements(By.css(selector))
  expect(elements, `expected at least one match for ${selector}`).to.not.have.lengthOf(0)

  for (const element of elements) {
    const box = await element.getRect()
    expect(box.width, `${selector} must have a non-zero width`).to.be.greaterThan(0)
    expect(box.height, `${selector} must have a non-zero height`).to.be.greaterThan(0)

    const visibility = await driver.executeScript<string>(
      'return getComputedStyle(arguments[0]).visibility',
      element,
    )
    expect(visibility, `${selector} must not be visibility:hidden`).to.equal('visible')
  }
}

describe('Phase 10 forced-colors smoke test', function () {
  this.timeout(60_000)

  let driver: WebDriver
  let server: AccessibilityServer

  before(async () => {
    server = await startAccessibilityServer()
    driver = await buildChromeDriver()
  })

  after(async () => {
    await driver?.quit()
    await server?.close()
  })

  it('keeps the chat composer usable and axe-clean under forced colors', async () => {
    await driver.get(`${server.origin}/bedrock-chat/ui/`)
    await driver.wait(until.elementLocated(By.css('textarea[aria-label="Message"]:not([disabled])')), 15_000)
    await enableForcedColors(driver)

    await assertVisible(driver, 'textarea[aria-label="Message"]')
    await assertVisible(driver, 'button[type="submit"]')
    await tabTo(driver, 'textarea[aria-label="Message"]')
    await assertNoAxeViolations(driver)
  })

  it('keeps the knowledge-base credibility badges legible under forced colors', async () => {
    await driver.get(`${server.origin}/bedrock-chat/dashboard/kb-browser`)
    await driver.wait(until.elementLocated(By.css('main tbody tr')), 15_000)
    await enableForcedColors(driver)

    // FR-DS-005: credibility is conveyed by icon + text, not color alone — both must still render.
    await assertVisible(driver, 'main tbody span svg')
    const badgeText = await driver.findElement(By.css('main tbody span')).getText()
    expect(badgeText.trim().length, 'credibility badge must still carry visible text').to.be.greaterThan(0)

    await tabTo(driver, 'main tbody button[aria-label^="Open "]')
    await assertNoAxeViolations(driver)
  })
})
