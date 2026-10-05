import { expect, jest } from '@jest/globals'
import { By, until, type WebDriver } from 'selenium-webdriver'

import { startAccessibilityServer, type AccessibilityServer } from './helpers/accessibility-server.js'
import { buildChromeDriver } from './helpers/webdriver.js'

describe('User menu and model picker', () => {
  jest.setTimeout(60_000)

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

  it('changes and persists the theme from the user dropdown', async () => {
    await driver.get(`${server.origin}/chat/ui/`)
    await driver.wait(until.elementLocated(By.css('textarea[aria-label="Message"]:not([disabled])')), 15_000)
    const account = await driver.wait(until.elementLocated(By.css('[data-slot="dropdown-menu-trigger"][aria-label="Accessibility tester"]')), 15_000)
    await account.click()
    await driver.wait(until.elementLocated(By.css('[data-slot="dropdown-menu-content"]')), 10_000)
    const themeItems = await driver.findElements(By.css('[data-slot="dropdown-menu-radio-item"]'))
    expect(await Promise.all(themeItems.map((item) => item.getText()))).toEqual(['Light', 'Dark', 'System'])
    await themeItems[1]?.click()

    await driver.wait(async () => (await driver.findElement(By.css('html')).getAttribute('class'))?.includes('dark'), 10_000)
    expect(await driver.executeScript('return localStorage.getItem("ui.theme")')).toBe('dark')
    await driver.navigate().refresh()
    await driver.wait(async () => (await driver.findElement(By.css('html')).getAttribute('class'))?.includes('dark'), 10_000)

    await driver.wait(until.elementLocated(By.css('textarea[aria-label="Message"]:not([disabled])')), 15_000)
    await (await driver.wait(until.elementLocated(By.css('[data-slot="dropdown-menu-trigger"][aria-label="Accessibility tester"]')), 10_000)).click()
    const restoredThemeItems = await driver.wait(until.elementsLocated(By.css('[data-slot="dropdown-menu-radio-item"]')), 10_000)
    await restoredThemeItems[2]?.click()
    expect(await driver.executeScript('return localStorage.getItem("ui.theme")')).toBe('system')
  })

  it('separates the model submenu from its parent when there is room', async () => {
    await driver.manage().window().setRect({ width: 768, height: 900 })
    await driver.get(`${server.origin}/chat/ui/`)
    const settings = await driver.wait(until.elementLocated(By.css('[aria-label="Model settings"]')), 15_000)
    await settings.click()
    const picker = await driver.wait(until.elementLocated(By.css('[data-slot="sheet-content"] [data-slot="dropdown-menu-trigger"]')), 10_000)
    await picker.click()
    const provider = await driver.wait(until.elementLocated(By.css('[data-slot="dropdown-menu-sub-trigger"]')), 10_000)
    if ((await driver.findElements(By.css('[data-slot="dropdown-menu-sub-content"]'))).length === 0) await provider.click()
    await driver.wait(until.elementLocated(By.css('[data-slot="dropdown-menu-sub-content"]')), 10_000)

    const geometry = await driver.executeScript<{
      readonly room: boolean
      readonly gap: number
      readonly withinViewport: boolean
      readonly height: number
    }>(`
      const parent = document.querySelector('[data-slot="dropdown-menu-content"]:not([data-slot="dropdown-menu-sub-content"])').getBoundingClientRect();
      const child = document.querySelector('[data-slot="dropdown-menu-sub-content"]').getBoundingClientRect();
      return {
        room: parent.left >= child.width + 6 || innerWidth - parent.right >= child.width + 6,
        gap: child.right <= parent.left ? parent.left - child.right : child.left >= parent.right ? child.left - parent.right : 0,
        withinViewport: child.left >= 0 && child.right <= innerWidth,
        height: child.height,
      };
    `)

    expect(geometry.withinViewport).toBe(true)
    expect(geometry.height).toBeLessThanOrEqual(630)
    if (geometry.room) expect(geometry.gap).toBeGreaterThanOrEqual(6)
  })
})
