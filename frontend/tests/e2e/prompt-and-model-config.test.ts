import { expect } from '@jest/globals'
import { By, Key, until, type WebDriver, type WebElement } from 'selenium-webdriver'

import { assertNoAxeViolations } from './helpers/axe.js'
import { startChatServer, type ChatServer, type ClientFrame } from './helpers/chat-server.js'
import { matchScreenshot } from './helpers/visual-snapshot.js'
import { buildChromeDriver } from './helpers/webdriver.js'

const LARGE_MODEL = {
  id: 'large-model',
  name: 'Large Model',
  provider: 'Provider A',
  supports_temperature: true,
  max_output_tokens: 8192,
}

const SMALL_MODEL = {
  id: 'small-model',
  name: 'Small Model',
  provider: 'Provider B',
  supports_temperature: true,
  max_output_tokens: 4096,
}

const MODEL_CONFIG = {
  modelId: LARGE_MODEL.id,
  modelDisplayName: LARGE_MODEL.name,
  overrideDefaults: { model_id: LARGE_MODEL.id, temperature: 0.7, max_tokens: 8192 },
  availableModels: [LARGE_MODEL, SMALL_MODEL],
  availableModelGroups: [
    { provider: LARGE_MODEL.provider, models: [LARGE_MODEL] },
    { provider: SMALL_MODEL.provider, models: [SMALL_MODEL] },
  ],
}

const visibleElement = async (driver: WebDriver, locator: By): Promise<WebElement> => {
  const element = await driver.wait(async () => {
    const elements = await driver.findElements(locator)
    for (const element of [...elements].reverse()) {
      if (await element.isDisplayed()) {
        return element
      }
    }
    return false
  }, 10_000)

  if (element === false) {
    throw new Error(`no visible element found for ${locator}`)
  }
  return element
}

const configUpdate = (frame: ClientFrame): Record<string, unknown> =>
  frame.config_overrides as Record<string, unknown>

// E4/E7/E8 (STD-002 section 3.4): drives the built SPA against an actual HTTP/WebSocket server.
describe('E4, E7 and E8 - prompt and model configuration', function () {

  let driver: WebDriver
  let server: ChatServer

  beforeAll(async () => {
    driver = await buildChromeDriver()
  })

  afterAll(async () => {
    await driver?.quit()
  })

  afterEach(async () => {
    await server?.close()
  })

  it('E4 fills a preset variable and sends the composed prompt', async () => {
    server = await startChatServer({
      config: {
        presetPrompts: [{
          id: 'workload-analysis',
          label: 'Workload Analysis',
          description: 'Analyze a workload job.',
          template: 'Analyze workload {{JOB_ID}}',
        }],
        variables: [{
          name: 'JOB_ID',
          label: 'Job ID',
          input_type: 'text',
          validate: 'nonempty',
        }],
      },
    })
    server.on('chat', () => undefined)

    await driver.get(`${server.origin}/bedrock-chat/ui`)
    const jobId = await driver.wait(until.elementLocated(By.css('#prompt-var-JOB_ID')), 10_000)
    await jobId.sendKeys('job-42')
    await (await visibleElement(driver, By.xpath("//button[contains(normalize-space(), 'Workload Analysis')]"))).click()

    await driver.wait(() => server.sentOf('chat').length === 1, 10_000)
    expect(server.sentOf('chat')[0]?.message).toBe('Analyze workload job-42')

    await assertNoAxeViolations(driver)
    await matchScreenshot(driver, 'e4-preset-with-variables')
  })

  it('E7 waits for model confirmation, clamps max_tokens, and updates the badge', async () => {
    server = await startChatServer({ config: MODEL_CONFIG })
    server.on('config_update', (frame) => {
      const update = configUpdate(frame)
      if (update.model_id === SMALL_MODEL.id) {
        return
      }
      if (update.max_tokens === 4096) {
        server.send({
          type: 'config_updated',
          active_overrides: { model_id: SMALL_MODEL.id, max_tokens: 4096 },
          applied_overrides: { max_tokens: 4096 },
          rejected_overrides: [],
        })
      }
    })
    server.on('chat', () => {
      server.send({
        type: 'ai_response',
        message: 'Configured model confirmed.',
        message_id: 'model-check-response',
        tool_calls: [],
        tool_results: [],
        conversation_id: 'model-check-conversation',
        metadata: {
          model_id: SMALL_MODEL.id,
          model_name: 'Server-confirmed Small Model',
          tool_call_rounds: 0,
          total_tool_calls: 0,
          preprocessing_applied: false,
        },
      })
    })

    await driver.get(`${server.origin}/bedrock-chat/ui`)
    const settings = await driver.wait(until.elementLocated(By.css('[aria-label="Model settings"]')), 10_000)
    expect(await settings.getText()).toContain(LARGE_MODEL.name)
    await driver.executeScript('arguments[0].click()', settings)

    const picker = await visibleElement(driver, By.xpath("//*[@data-slot='sheet-content']//button[normalize-space()='Large Model']"))
    await picker.click()
    const provider = await visibleElement(driver, By.xpath("//*[@role='menuitem'][normalize-space()='Provider B']"))
    await provider.click()
    const model = await visibleElement(driver, By.xpath("//*[@role='menuitemradio'][contains(normalize-space(), 'Small Model')]"))
    await model.click()

    await driver.wait(() => server.sentOf('config_update').length === 1, 10_000)
    expect(configUpdate(server.sentOf('config_update')[0] ?? { type: '' })).toEqual({ model_id: SMALL_MODEL.id })
    expect(await settings.getText()).toContain(LARGE_MODEL.name)
    expect(await settings.findElements(By.css('[data-slot="badge"]'))).toHaveLength(0)
    expect(await driver.findElements(By.xpath("//*[@role='status']//*[normalize-space()='Waiting for server confirmation']"))).not.toHaveLength(0)

    server.send({
      type: 'config_updated',
      active_overrides: { model_id: SMALL_MODEL.id },
      applied_overrides: { model_id: SMALL_MODEL.id },
      rejected_overrides: [],
    })

    await driver.wait(() => server.sentOf('config_update').length === 2, 10_000)
    expect(configUpdate(server.sentOf('config_update')[1] ?? { type: '' })).toEqual({ max_tokens: 4096 })
    await driver.wait(async () => (await settings.getText()).includes(SMALL_MODEL.name), 10_000)
    expect(await settings.getText()).toContain('2')

    const maxTokens = await driver.findElement(By.css('[data-slot="sheet-content"] #override-max_tokens'))
    expect(await maxTokens.getAttribute('max')).toBe('4096')
    expect(await maxTokens.getAttribute('value')).toBe('4096')

    await assertNoAxeViolations(driver, { include: '[data-slot="sheet-content"]' })
    await matchScreenshot(driver, 'e7-confirmed-model-clamp')

    await driver.actions().sendKeys(Key.ESCAPE).perform()
    const composer = await driver.findElement(By.css('textarea'))
    await composer.sendKeys('Confirm configured model', Key.ENTER)
    await driver.wait(async () => (await settings.getText()).includes('Server-confirmed Small Model'), 10_000)
  })

  it('E8 resets confirmed overrides and restores every control default', async () => {
    server = await startChatServer({ config: MODEL_CONFIG })
    server.on('config_update', (frame) => {
      const update = configUpdate(frame)
      server.send({
        type: 'config_updated',
        active_overrides: update,
        applied_overrides: update,
        rejected_overrides: [],
      })
    })
    server.on('config_reset', () => {
      server.send({
        type: 'config_updated',
        active_overrides: {},
        applied_overrides: {},
        rejected_overrides: [],
      })
    })

    await driver.get(`${server.origin}/bedrock-chat/ui`)
    const settings = await driver.wait(until.elementLocated(By.css('[aria-label="Model settings"]')), 10_000)
    await driver.executeScript('arguments[0].click()', settings)

    const temperature = await driver.findElement(By.css('[data-slot="sheet-content"] input[aria-label="Temperature"]'))
    await temperature.sendKeys(Key.ARROW_LEFT)

    await driver.wait(() => server.sentOf('config_update').length === 1, 10_000, 'config_update was not sent')
    await driver.wait(async () => (await settings.findElements(By.css('[data-slot="badge"]'))).length === 1, 10_000, 'override badge did not appear')
    await (await visibleElement(driver, By.xpath("//button[normalize-space()='Reset to defaults']"))).click()

    await driver.wait(() => server.sentOf('config_reset').length === 1, 10_000, 'config_reset was not sent')
    await driver.wait(async () => (await settings.findElements(By.css('[data-slot="badge"]'))).length === 0, 10_000, 'override badge did not clear')
    const resetTemperature = await driver.findElement(By.css('[data-slot="sheet-content"] input[aria-label="Temperature"]'))
    expect(await resetTemperature.getAttribute('value')).toBe('0.7')
    expect(await driver.findElements(By.css('[data-slot="sheet-content"] [data-slot="override-marker"]'))).toHaveLength(0)

    await assertNoAxeViolations(driver, { include: '[data-slot="sheet-content"]' })
    await matchScreenshot(driver, 'e8-reset-model-defaults')
  })
})
