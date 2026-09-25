import { jest } from '@jest/globals'
import { expect } from '@jest/globals'
import { By, Key, until, type WebDriver, type WebElement } from 'selenium-webdriver'

import { assertNoAxeViolations } from './helpers/axe.js'
import {
  startAccessibilityServer,
  type AccessibilityServer,
} from './helpers/accessibility-server.js'
import { buildChromeDriver } from './helpers/webdriver.js'

type Theme = 'light' | 'dark'

type RouteCase = {
  readonly name: string
  readonly path: string
  readonly expectedPath: string
  readonly heading: string
  readonly ready: string
  readonly journey: (driver: WebDriver, server: AccessibilityServer) => Promise<void>
}

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
      const hasVisibleFocus = await driver.executeScript<boolean>(
        `const element = document.activeElement;
         const style = getComputedStyle(element);
         return element.matches(':focus-visible') &&
           ((style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0) ||
            style.boxShadow !== 'none');`,
      )
      expect({ selector, hasVisibleFocus }).toEqual({ selector, hasVisibleFocus: true })
      return element
    }
  }

  throw new Error(`keyboard traversal did not reach ${selector}`)
}

const chatJourney = async (driver: WebDriver, server: AccessibilityServer): Promise<void> => {
  const sentBefore = server.sentOf('chat').length
  const composer = await tabTo(driver, 'textarea[aria-label="Message"]')
  await composer.sendKeys('Keyboard-only route check', Key.ENTER)
  await driver.wait(() => server.sentOf('chat').length === sentBefore + 1, 10_000)
}

const openFirstRow = async (driver: WebDriver): Promise<void> => {
  const rowAction = await tabTo(driver, 'main tbody button[aria-label^="Open "]')
  await rowAction.sendKeys(Key.ENTER)
  await driver.wait(until.elementLocated(By.css('[data-slot="sheet-content"]')), 10_000)
}

const navigateToKnowledge = async (driver: WebDriver): Promise<void> => {
  const link = await tabTo(driver, 'a[href="/bedrock-chat/dashboard/kb-browser"]')
  await link.sendKeys(Key.ENTER)
  await driver.wait(async () => new URL(await driver.getCurrentUrl()).pathname === '/bedrock-chat/dashboard/kb-browser', 10_000)
}

const filterUsageByUser = async (driver: WebDriver): Promise<void> => {
  const userInput = await tabTo(driver, 'input[type="search"]')
  await userInput.sendKeys('alice@example.com', Key.ENTER)
  await driver.wait(async () => new URL(await driver.getCurrentUrl()).searchParams.get('user') === 'alice@example.com', 10_000)
}

const ROUTES: readonly RouteCase[] = [
  { name: 'new conversation', path: '/bedrock-chat/ui/', expectedPath: '/bedrock-chat/ui/', heading: 'Workload Analyzer', ready: 'textarea[aria-label="Message"]:not([disabled])', journey: chatJourney },
  { name: 'loaded conversation', path: '/bedrock-chat/ui/c/conversation-1', expectedPath: '/bedrock-chat/ui/c/conversation-1', heading: 'Workload Analyzer', ready: 'textarea[aria-label="Message"]:not([disabled])', journey: chatJourney },
  { name: 'admin redirect', path: '/bedrock-chat/dashboard', expectedPath: '/bedrock-chat/dashboard/feedback', heading: 'Feedback queue', ready: 'main tbody button[aria-label^="Open "]', journey: openFirstRow },
  { name: 'feedback queue', path: '/bedrock-chat/dashboard/feedback', expectedPath: '/bedrock-chat/dashboard/feedback', heading: 'Feedback queue', ready: 'main tbody button[aria-label^="Open "]', journey: openFirstRow },
  { name: 'reviewed feedback', path: '/bedrock-chat/dashboard/reviewed', expectedPath: '/bedrock-chat/dashboard/reviewed', heading: 'Reviewed', ready: 'main tbody button[aria-label^="Open "]', journey: openFirstRow },
  { name: 'feedback stats', path: '/bedrock-chat/dashboard/feedback/stats', expectedPath: '/bedrock-chat/dashboard/feedback/stats', heading: 'Feedback stats', ready: 'main table', journey: navigateToKnowledge },
  { name: 'knowledge base', path: '/bedrock-chat/dashboard/kb-browser', expectedPath: '/bedrock-chat/dashboard/kb-browser', heading: 'Knowledge base', ready: 'main tbody button[aria-label^="Open "]', journey: openFirstRow },
  { name: 'usage analytics', path: '/bedrock-chat/dashboard/token-usages', expectedPath: '/bedrock-chat/dashboard/token-usages', heading: 'Usage', ready: 'input[type="search"]', journey: filterUsageByUser },
]

describe('Phase 10 accessibility route matrix', function () {
  jest.setTimeout(180_000)

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
    for (const route of ROUTES) {
      it(`${route.name} has no axe violations and completes by keyboard in ${theme} theme`, async () => {
        await driver.get(`${server.origin}/bedrock-chat/ui/`)
        await driver.executeScript("localStorage.setItem('ui.theme', arguments[0])", theme)
        await driver.get(`${server.origin}${route.path}`)

        const heading = await driver.wait(until.elementLocated(By.css('h1')), 15_000)
        await driver.wait(until.elementLocated(By.css(route.ready)), 15_000)
        await driver.wait(
          async () => ((await driver.findElement(By.css('html')).getAttribute('class')) ?? '').split(/\s+/).includes(theme),
          10_000,
        )

        expect(new URL(await driver.getCurrentUrl()).pathname).toBe(route.expectedPath)
        expect(await heading.getText()).toBe(route.heading)
        await assertNoAxeViolations(driver)
        await route.journey(driver, server)
      })
    }
  }
})