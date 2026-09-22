import { By, until, type WebDriver } from 'selenium-webdriver'

import { startAccessibilityServer, type AccessibilityServer } from './helpers/accessibility-server.js'
import { matchScreenshot } from './helpers/visual-snapshot.js'
import { buildChromeDriver } from './helpers/webdriver.js'

type Theme = 'light' | 'dark'
type Viewport = { readonly name: string; readonly width: number; readonly height: number }

// SPEC-020 §8: chat view, admin queue, KB editor and settings sheet, both themes, 320/768/1440 px.
const VIEWPORTS: readonly Viewport[] = [
  { name: '320', width: 320, height: 640 },
  { name: '768', width: 768, height: 1024 },
  { name: '1440', width: 1440, height: 900 },
]

type ViewCase = {
  readonly name: string
  readonly open: (driver: WebDriver, origin: string) => Promise<void>
}

const VIEWS: readonly ViewCase[] = [
  {
    name: 'chat',
    open: async (driver, origin) => {
      await driver.get(`${origin}/bedrock-chat/ui/`)
      await driver.wait(until.elementLocated(By.css('textarea[aria-label="Message"]:not([disabled])')), 15_000)
    },
  },
  {
    name: 'admin-queue',
    open: async (driver, origin) => {
      await driver.get(`${origin}/bedrock-chat/dashboard/feedback`)
      await driver.wait(until.elementLocated(By.css('main tbody button[aria-label^="Open "]')), 15_000)
    },
  },
  {
    name: 'kb-editor',
    open: async (driver, origin) => {
      await driver.get(`${origin}/bedrock-chat/dashboard/kb-browser`)
      const rowAction = await driver.wait(until.elementLocated(By.css('main tbody button[aria-label^="Open "]')), 15_000)
      await rowAction.click()
      await driver.wait(until.elementLocated(By.css('[data-slot="sheet-content"]')), 15_000)
    },
  },
  {
    name: 'settings',
    open: async (driver, origin) => {
      await driver.get(`${origin}/bedrock-chat/ui/`)
      const settings = await driver.wait(until.elementLocated(By.css('[aria-label="Model settings"]')), 15_000)
      await driver.executeScript('arguments[0].click()', settings)
      await driver.wait(until.elementLocated(By.css('[data-slot="sheet-content"]')), 15_000)
    },
  },
]

const setTheme = async (driver: WebDriver, origin: string, theme: Theme): Promise<void> => {
  await driver.get(`${origin}/bedrock-chat/ui/`)
  await driver.executeScript("localStorage.setItem('ui.theme', arguments[0])", theme)
}

const waitForTheme = async (driver: WebDriver, theme: Theme): Promise<void> => {
  await driver.wait(
    async () => ((await driver.findElement(By.css('html')).getAttribute('class')) ?? '').split(/\s+/).includes(theme),
    10_000,
  )
}

// T-185 (PLAN-002, SPEC-020 §8): visual regression baselines for every combination of view,
// theme and viewport. First run records a baseline PNG per combination; reruns fail on any
// byte-for-byte drift (see helpers/visual-snapshot.ts).
describe('Phase 10 visual regression baselines', function () {
  this.timeout(180_000)

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

  for (const theme of ['light', 'dark'] as const satisfies readonly Theme[]) {
    for (const viewport of VIEWPORTS) {
      for (const view of VIEWS) {
        it(`${view.name} matches its ${theme}/${viewport.name}px baseline`, async () => {
          await driver.manage().window().setRect({ width: viewport.width, height: viewport.height })
          await setTheme(driver, server.origin, theme)
          await view.open(driver, server.origin)
          await waitForTheme(driver, theme)

          await matchScreenshot(driver, `vr-${view.name}-${theme}-${viewport.name}`)
        })
      }
    }
  }
})
