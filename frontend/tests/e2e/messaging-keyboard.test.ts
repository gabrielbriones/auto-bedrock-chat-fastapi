import { expect } from '@jest/globals'
import { By, Key, until, type WebDriver } from 'selenium-webdriver'

import { assertNoAxeViolations } from './helpers/axe.js'
import { startChatServer, type ChatServer } from './helpers/chat-server.js'
import { buildChromeDriver } from './helpers/webdriver.js'

// E7 (STD-002 §3.4) — T-079/NFR-A11Y-002. The composer's keyboard matrix and autosizing depend on
// selection APIs, real layout and `scrollHeight`, none of which jsdom implements: the component
// tests prove the intent, this proves the behaviour in a browser.
describe('E7 — messaging keyboard journey', function () {

  let driver: WebDriver
  let server: ChatServer

  const composer = () =>
    driver.wait(until.elementLocated(By.css('textarea[aria-label="Message"]')), 15_000)

  const composerIsFocused = () =>
    driver.executeScript<boolean>(
      'return document.activeElement?.getAttribute("aria-label") === "Message"',
    )

  const composerHeight = () =>
    driver.executeScript<number>(
      'return Math.round(parseFloat(document.querySelector(\'textarea[aria-label="Message"]\').style.height))',
    )

  beforeAll(async () => {
    driver = await buildChromeDriver()
  })

  afterAll(async () => {
    await driver?.quit()
  })

  beforeEach(async () => {
    server = await startChatServer()
  })

  afterEach(async () => {
    await server.close()
  })

  const answer = (message: string, messageId: string) => {
    server.send({
      type: 'ai_response',
      message,
      message_id: messageId,
      conversation_id: 'conv-1',
      tool_calls: [],
      tool_results: [],
      metadata: {
        model_id: 'claude',
        model_name: 'Claude',
        tool_call_rounds: 0,
        total_tool_calls: 0,
        preprocessing_applied: false,
      },
    })
  }

  it('sends with Enter, keeps modified Enter for newlines, and hands focus back on unlock', async () => {
    server.on('chat', () => {
      server.send({ type: 'typing', message: 'Look' })
      server.send({ type: 'typing', message: 'Looking at it' })
      // Held back so the in-flight lock is observable rather than a race with the assertion.
      setTimeout(() => answer('IPC is 2.1', 'm-1'), 1_000)
    })

    await driver.get(`${server.origin}/bedrock-chat/ui/`)
    const field = await composer()
    await field.click()

    // Shift+Enter and Ctrl+Enter both break the line; only a bare Enter leaves the client.
    await field.sendKeys('first', Key.chord(Key.SHIFT, Key.ENTER), 'second')
    await field.sendKeys(Key.chord(Key.CONTROL, Key.ENTER), 'third')
    expect(server.sentOf('chat')).toHaveLength(0)

    await field.sendKeys(Key.ENTER)
    await driver.wait(async () => server.sentOf('chat').length === 1, 10_000)
    expect(server.sentOf('chat')[0]?.message).toBe('first\nsecond\nthird')

    // FR-MSG-012: locked until the turn resolves, then focused again without a mouse.
    await driver.wait(until.elementLocated(By.css('textarea[aria-label="Message"][disabled]')), 5_000)
    await driver.wait(
      until.elementLocated(By.xpath("//article[contains(., 'IPC is 2.1')]")),
      10_000,
    )
    await driver.wait(async () => composerIsFocused(), 5_000)
    expect(await (await composer()).getAttribute('disabled')).toBe(null)

    // Scoped to the chat region: `page-has-heading-one` belongs to the shell, not to this ticket.
    await assertNoAxeViolations(driver, { include: 'main' })
  })

  it('grows the composer with its content and stops at the cap (FR-MSG-011)', async () => {
    await driver.get(`${server.origin}/bedrock-chat/ui/`)
    const field = await composer()
    await field.click()

    await field.sendKeys('one')
    // The textarea starts two rows tall, so growth only begins once the content outgrows that.
    const base = await composerHeight()

    for (let line = 0; line < 4; line += 1) {
      await field.sendKeys(Key.chord(Key.SHIFT, Key.ENTER), `line ${line}`)
    }
    expect(await composerHeight()).toBeGreaterThan(base)

    for (let line = 0; line < 20; line += 1) {
      await field.sendKeys(Key.chord(Key.SHIFT, Key.ENTER), `line ${line}`)
    }

    expect(await composerHeight()).toBe(150)
    expect(
      await driver.executeScript<string>(
        'return document.querySelector(\'textarea[aria-label="Message"]\').style.overflowY',
      ),
    ).toBe('auto')
  })

  it('reaches the send action from the composer by keyboard alone', async () => {
    server.on('chat', () => {
      answer('A grounded answer', 'm-2')
    })

    await driver.get(`${server.origin}/bedrock-chat/ui/`)
    await (await composer()).click()
    await (await composer()).sendKeys('what is the bottleneck?', Key.ENTER)

    await driver.wait(
      until.elementLocated(By.xpath("//article[contains(., 'A grounded answer')]")),
      10_000,
    )

    // NFR-A11Y-002: with a draft to send, the send action is the composer's next tab stop rather
    // than a mouse-only target.
    await driver.wait(async () => composerIsFocused(), 5_000)
    await (await composer()).sendKeys('and why?')
    await driver.actions().sendKeys(Key.TAB).perform()

    expect(
      await driver.executeScript<string>(
        'return document.activeElement?.getAttribute("aria-label") ?? ""',
      ),
    ).toBe('Send')

    await assertNoAxeViolations(driver, { include: 'main' })
  })
})
