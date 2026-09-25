import { readFile } from 'node:fs/promises'
import { createServer, type Server } from 'node:http'
import path from 'node:path'

import { expect } from '@jest/globals'
import { By, Key, until, type WebDriver } from 'selenium-webdriver'

import { buildChromeDriver } from './helpers/webdriver.js'

const contentType = (filePath: string): string => {
  if (filePath.endsWith('.js')) return 'text/javascript'
  if (filePath.endsWith('.css')) return 'text/css'
  if (filePath.endsWith('.json')) return 'application/json'
  return 'text/html'
}

// SPEC-010 §7 "Component"/"E2E": the focus trap and Escape behaviour depend on `inert`, which
// jsdom does not implement — so they can only be proven in a real browser.
describe('Auth dialog keyboard journey', function () {

  let driver: WebDriver
  let server: Server
  let origin: string
  let baseConfig: Record<string, unknown>
  let requireAuth = true

  beforeAll(async () => {
    const dist = path.resolve(process.cwd(), 'dist')
    const raw = await readFile(
      path.resolve(process.cwd(), 'tests/msw/fixtures/bootstrap-config.json'),
      'utf8',
    )
    baseConfig = {
      ...(JSON.parse(raw) as Record<string, unknown>),
      authEnabled: true,
      supportedAuthTypes: ['bearer_token', 'basic_auth'],
      defaultAuthType: 'basic_auth',
      ssoEnabled: false,
      ssoAuthenticated: false,
      ssoUserDisplay: null,
    }

    server = createServer(async (request, response) => {
      const requestPath = new URL(request.url ?? '/', 'http://localhost').pathname

      if (requestPath === '/bedrock-chat/config') {
        response.writeHead(200, { 'content-type': 'application/json' })
        response.end(JSON.stringify({ ...baseConfig, requireAuth }))
        return
      }

      if (requestPath === '/bedrock-chat/admin/_capabilities') {
        response.writeHead(200, { 'content-type': 'application/json' })
        response.end(JSON.stringify({ is_admin: false, anonymous: false, token_usage_enabled: false }))
        return
      }

      const relativePath = requestPath.startsWith('/bedrock-chat/ui/assets/')
        ? requestPath.slice('/bedrock-chat/ui/'.length)
        : 'index.html'

      try {
        const body = await readFile(path.join(dist, relativePath))
        response.writeHead(200, { 'content-type': contentType(relativePath) })
        response.end(body)
      } catch {
        response.writeHead(404)
        response.end()
      }
    })

    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const address = server.address()
    if (address === null || typeof address === 'string') {
      throw new Error('test server did not expose a TCP port')
    }
    origin = `http://127.0.0.1:${address.port}`
    driver = await buildChromeDriver()
  })

  afterAll(async () => {
    await driver?.quit()
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error === undefined ? resolve() : reject(error)))
    })
  })

  const isRootInert = () =>
    driver.executeScript<boolean>(
      'return document.getElementById("root")?.hasAttribute("inert") ?? false',
    )

  const openDialog = async (authRequired = true) => {
    requireAuth = authRequired
    await driver.get(`${origin}/bedrock-chat/ui/`)
    await driver.wait(until.elementLocated(By.css('[role="dialog"]')), 15_000)
    // The route tree is code-split, so the page behind the dialog mounts a beat later. Waiting for
    // it is the point of the exercise: content that arrives after the dialog opened must be
    // trapped too.
    return driver.wait(until.elementLocated(By.css('#root header')), 15_000)
  }

  // Base UI's focus guards restore focus inside a `requestAnimationFrame`, so a tab has to settle
  // before `document.activeElement` means anything.
  const focusAfterTab = async (shift: boolean): Promise<string> => {
    const actions = driver.actions()
    await (shift
      ? actions.keyDown(Key.SHIFT).sendKeys(Key.TAB).keyUp(Key.SHIFT)
      : actions.sendKeys(Key.TAB)
    ).perform()

    return driver.executeAsyncScript<string>(`
      const done = arguments[arguments.length - 1]
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const active = document.activeElement
        const dialog = document.querySelector('[role="dialog"]')
        const label = (active && (active.getAttribute('name') || active.textContent || '').trim()) || ''
        const where = dialog && active && dialog.contains(active) ? 'INSIDE' : 'OUTSIDE'
        done(where + ':' + (active ? active.tagName : 'null') + '[' + label + ']')
      }))
    `)
  }

  const tabTrail = async (steps: number, shift = false): Promise<string[]> => {
    const trail: string[] = []
    for (let step = 0; step < steps; step += 1) {
      trail.push(await focusAfterTab(shift))
    }
    return trail
  }

  for (const authRequired of [true, false]) {
    const suffix = `requireAuth=${authRequired}`

    // FR-IAM-013 / NFR-A11Y-002: the page behind the dialog holds no tab stops and is not
    // announced — asserted on the root itself rather than inferred from a role query.
    it(`inerts the application root while the dialog is open (${suffix})`, async () => {
      await openDialog(authRequired)

      expect(await isRootInert()).toBe(true)
    })

    it(`traps Tab and Shift+Tab inside the dialog (${suffix})`, async () => {
      await openDialog(authRequired)

      const forwards = await tabTrail(12)
      const backwards = await tabTrail(12, true)

      expect(forwards.filter((entry) => entry.startsWith('OUTSIDE'))).toEqual([])
      expect(backwards.filter((entry) => entry.startsWith('OUTSIDE'))).toEqual([])
    })
  }

  // FR-IAM-013 (Escape half): with auth required there is no way out of the dialog.
  it('ignores Escape while authentication is required', async () => {
    await openDialog()

    await driver.actions().sendKeys(Key.ESCAPE).perform()

    expect(await driver.findElements(By.css('[role="dialog"]'))).toHaveLength(1)
  })

  // FR-IAM-008: with auth required there is no Skip control to reach by keyboard.
  it('offers no skip control when authentication is required', async () => {
    await openDialog()

    const labels = await Promise.all(
      (await driver.findElements(By.css('[role="dialog"] button'))).map((button) => button.getText()),
    )

    expect(labels.some((label) => label.trim() === 'Skip')).toBe(false)
  })

  // FR-IAM-008: dismissing an optional dialog releases the page behind it again.
  it('releases the application root when an optional dialog is skipped', async () => {
    await openDialog(false)

    await driver
      .findElement(By.xpath('//*[@role="dialog"]//button[normalize-space()="Skip"]'))
      .click()
    await driver.wait(
      async () => (await driver.findElements(By.css('[role="dialog"]'))).length === 0,
      5_000,
    )

    expect(await isRootInert()).toBe(false)
  })

  // FR-IAM-004
  it('moves focus to the first invalid field on an empty submit', async () => {
    await openDialog()

    const submit = await driver.findElement(By.css('[role="dialog"] button[type="submit"]'))
    await submit.click()

    const focusedName = await driver.executeScript<string>(
      'return document.activeElement?.getAttribute("name") ?? ""',
    )

    expect(focusedName).toBe('username')
  })
})
