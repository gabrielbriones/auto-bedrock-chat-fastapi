import { expect } from '@jest/globals'
import { By, Key, until, type WebDriver } from 'selenium-webdriver'

import { startChatServer, type ChatServer } from './helpers/chat-server.js'
import { buildChromeDriver } from './helpers/webdriver.js'

describe('Chat welcome dismissal and scroll bounds', () => {

  let driver: WebDriver
  let server: ChatServer

  beforeAll(async () => {
    driver = await buildChromeDriver()
  })

  afterAll(async () => {
    await driver?.quit()
  })

  beforeEach(async () => {
    server = await startChatServer({
      config: {
        presetPrompts: [{
          id: 'analysis',
          label: 'Workload Analysis',
          description: 'Analyze a workload.',
          template: 'Analyze {{JOB_ID}}',
        }],
        variables: [{ name: 'JOB_ID', label: 'Job ID', input_type: 'text' }],
      },
    })
  })

  afterEach(async () => {
    await server?.close()
  })

  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    it(`keeps a long conversation inside the viewport at ${viewport.width}px`, async () => {
      await driver.manage().window().setRect(viewport)
      await driver.get(`${server.origin}/bedrock-chat/ui/`)
      const field = await driver.wait(until.elementLocated(By.css('textarea[aria-label="Message"]')), 10_000)
      await driver.wait(until.elementIsEnabled(field), 10_000)
      expect(await driver.findElements(By.css('[aria-label="Welcome"]'))).toHaveLength(1)

      await field.sendKeys('Analyze my workload', Key.ENTER)
      await driver.wait(() => server.sentOf('chat').length === 1, 10_000)
      expect(await driver.findElements(By.css('[aria-label="Welcome"], [aria-label="Preset prompts"], #prompt-var-JOB_ID'))).toHaveLength(0)

      server.send({
        type: 'ai_response',
        message: Array.from({ length: 60 }, (_, index) => `Paragraph ${index}: workload analysis output.`).join('\n\n'),
        message_id: 'long-answer',
        conversation_id: 'scroll-conversation',
        tool_calls: [],
        tool_results: [],
        metadata: { model_id: 'test', model_name: 'Test', tool_call_rounds: 0, total_tool_calls: 0, preprocessing_applied: false },
      })
      await driver.wait(until.elementLocated(By.xpath("//article[contains(., 'Paragraph 59:')]")), 10_000)
      await driver.wait(until.elementIsEnabled(field), 10_000)

      const bounds = await driver.executeScript<{
        documentHeight: number
        viewportHeight: number
        documentWidth: number
        viewportWidth: number
        mainHeight: number
        mainScrollHeight: number
        bottomGap: number
      }>(`
        const main = document.querySelector('main');
        main.scrollTop = main.scrollHeight;
        return {
          documentHeight: document.documentElement.scrollHeight,
          viewportHeight: innerHeight,
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: innerWidth,
          mainHeight: main.clientHeight,
          mainScrollHeight: main.scrollHeight,
          bottomGap: main.getBoundingClientRect().bottom - document.querySelector('form').getBoundingClientRect().bottom,
        };
      `)

      expect(bounds.documentHeight).toBeLessThanOrEqual(bounds.viewportHeight)
      expect(bounds.documentWidth).toBeLessThanOrEqual(bounds.viewportWidth)
      expect(bounds.mainScrollHeight).toBeGreaterThan(bounds.mainHeight)
      expect(Math.abs(bounds.bottomGap)).toBeLessThanOrEqual(1)
    })
  }
})