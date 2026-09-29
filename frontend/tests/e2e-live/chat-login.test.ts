import { expect, jest } from '@jest/globals'
import { By, Key, until, type WebDriver } from 'selenium-webdriver'

import { buildChromeDriver } from '../e2e/helpers/webdriver.js'

jest.setTimeout(480_000)

describe('Live backend: chat login', () => {
  let driver: WebDriver
  let origin: string
  let requireAuth: boolean | undefined
  let ssoEnabled: boolean | undefined

  beforeAll(async () => {
    const configuredOrigin = process.env.TEST_ORIGIN
    if (!configuredOrigin) {
      throw new Error('Set TEST_ORIGIN to the Vite origin backed by a disposable FastAPI instance.')
    }
    const url = new URL(configuredOrigin)
    if (!['http:', 'https:'].includes(url.protocol) || url.pathname !== '/' || url.search || url.hash) {
      throw new Error('TEST_ORIGIN must be an HTTP(S) origin without a path or query.')
    }
    origin = url.origin

    const response = await fetch(`${origin}/bedrock-chat/config`)
    if (!response.ok) throw new Error(`Live bootstrap returned HTTP ${response.status}`)
    const config = (await response.json()) as { requireAuth?: boolean; ssoEnabled?: boolean }
    requireAuth = config.requireAuth
    ssoEnabled = config.ssoEnabled

    const proxy = process.env.HTTPS_PROXY ?? process.env.https_proxy
    const proxyArgs: string[] = []
    if (proxy) {
      const url = new URL(proxy)
      if (url.protocol !== 'http:' || url.username || url.password) {
        throw new Error('The live Chrome test supports only an unauthenticated HTTP HTTPS_PROXY.')
      }
      proxyArgs.push(`--proxy-server=${url.origin}`, '--proxy-bypass-list=localhost;127.0.0.1;[::1]')
    }
    driver = await buildChromeDriver(proxyArgs, false, 'none')
  })

  afterAll(async () => {
    await driver?.quit()
  })

  afterEach(async () => {
    if (process.env.HEADED === '1') {
      await new Promise((resolve) => setTimeout(resolve, 10_000))
    }
  })

  const startSsoLogin = async () => {
    const dialog = await driver.wait(until.elementLocated(By.css('[role="dialog"]')), 20_000)
    await driver.wait(until.elementIsVisible(dialog), 10_000)
    const composer = await driver.wait(
      until.elementLocated(By.css('textarea[aria-label="Message"]')),
      20_000,
    )
    expect(await composer.isEnabled()).toBe(false)

    const loginButton = By.xpath(
      ".//p[contains(., 'Click below to log in')]/following-sibling::button[normalize-space()='Login with SSO']",
    )
    if ((await dialog.findElements(loginButton)).length === 0) {
      await (await dialog.findElement(By.css('[role="combobox"]'))).click()
      await (await driver.wait(until.elementLocated(By.xpath("//*[@role='option'][normalize-space()='Login with SSO']")), 10_000)).click()
    }
    await (await dialog.findElement(loginButton)).click()
  }

  it('loads the real bootstrap into the chat shell', async () => {
    await driver.get(`${origin}/bedrock-chat/ui/`)
    const composer = await driver.wait(
      until.elementLocated(By.css('textarea[aria-label="Message"]')),
      20_000,
    )
    expect(await composer.getAttribute('aria-label')).toBe('Message')
  })

  const idpPreflight = process.env.RUN_IDP_PREFLIGHT === '1' ? it : it.skip
  idpPreflight('reaches the SSO sign-in page through the browser proxy', async () => {
    if (!requireAuth || !ssoEnabled) return
    await driver.get(`${origin}/bedrock-chat/ui/`)
    await startSsoLogin()
    const page = await driver.wait(async () => {
      const current = await driver.getCurrentUrl()
      if (current.startsWith('chrome-error://')) return false
      if (!current.startsWith('https://')) return false
      return driver.executeScript<{ title: string; errorCode: string | null }>(`
        if (document.readyState !== 'complete' || !document.title || document.title === 'Loading...') return null
        return { title: document.title, errorCode: document.querySelector('#error-code')?.textContent ?? null }
      `)
    }, 30_000, 'Chrome could not load the SSO sign-in page; check HTTPS_PROXY and VPN access')
    if (!page) throw new Error('SSO sign-in page did not finish loading')
    const host = new URL(await driver.getCurrentUrl()).host
    process.stderr.write(`SSO page host: ${host}; title: ${page.title}; browser error: ${page.errorCode ?? 'none'}\n`)
    expect(page.errorCode).toBeNull()
  })

  it('logs in with SSO, sends a prompt and renders the backend response', async () => {
    await driver.get('about:blank')
    await driver.wait(async () => (await driver.getCurrentUrl()) === 'about:blank', 10_000)
    await driver.get(`${origin}/bedrock-chat/ui/`)
    if (requireAuth) {
      const session = await driver.wait(async () => {
        const fields = await driver.findElements(By.css('textarea[aria-label="Message"]'))
        if (fields.length === 0) return false
        if (await fields[0]!.isEnabled()) return 'authenticated'
        const dialogs = await driver.findElements(By.css('[role="dialog"]'))
        return dialogs.length > 0 && (await dialogs[0]!.isDisplayed()) ? 'login' : false
      }, 20_000, 'Neither an authenticated chat nor a sign-in dialog appeared')
      if (session === 'login') {
        if (!ssoEnabled || process.env.HEADED !== '1') {
          throw new Error('Live chat requires SSO: use test:e2e:live:headed with an SSO-enabled backend.')
        }
        await startSsoLogin()
        process.stderr.write('Complete SSO sign-in in the visible Chrome window; waiting up to 5 minutes for chat to unlock.\n')
        await driver.wait(async () => {
          if (!(await driver.getCurrentUrl()).startsWith(`${origin}/bedrock-chat/ui/`)) return false
          const fields = await driver.findElements(By.css('textarea[aria-label="Message"]'))
          return fields.length > 0 && (await fields[0]!.isEnabled())
        }, 300_000, 'SSO did not return to the chat with an enabled composer within 5 minutes')
      }
    }

    const composer = await driver.wait(
      until.elementLocated(By.css('textarea[aria-label="Message"]:not([disabled])')),
      20_000,
    )
    const prompt = `Live smoke ${Date.now()}: reply with a short greeting.`
    await composer.sendKeys(prompt, Key.ENTER)

    await driver.wait(async () => {
      const userMessages = await driver.findElements(By.css('article[aria-label="You"]'))
      for (const message of userMessages) {
        if ((await message.getText()).includes(prompt)) return true
      }
      return false
    }, 15_000)

    let responseText = ''
    await driver.wait(async () => {
      const messages = await driver.findElements(By.css('article[aria-label="Assistant"] p'))
      for (const message of messages) {
        responseText = (await message.getText()).trim()
        if (responseText) return true
      }
      return false
    }, 90_000, 'No assistant response arrived from the live backend')

    expect(responseText).not.toBe('')
  })
})