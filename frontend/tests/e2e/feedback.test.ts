import { expect } from 'chai'
import { By, Key, until, type WebDriver } from 'selenium-webdriver'

import { assertNoAxeViolations } from './helpers/axe.js'
import { startChatServer, conversationSummary, type ChatServer } from './helpers/chat-server.js'
import { matchScreenshot } from './helpers/visual-snapshot.js'
import { buildChromeDriver } from './helpers/webdriver.js'

const ROSTER = [
  conversationSummary('conv-1', 'GEMM tuning', 1),
  conversationSummary('conv-2', 'Stream triad', 30),
]

const MESSAGE_ID = 'm-9'
const CORRECTION = 'The measured IPC is 1.8, not 0.9.'

// E9 (STD-002 §3.4): negative rating with a correction → submitted → the submitted state survives a
// conversation switch. Drives the built bundle against a scripted backend, so the `feedback` /
// `feedback_ack` / `feedback_error` wire contract of SPEC-015 is exercised rather than mocked.
describe('E9 — feedback', function () {
  this.timeout(60_000)

  let driver: WebDriver
  let server: ChatServer

  const byText = async (text: string) =>
    driver.wait(until.elementLocated(By.xpath(`//*[normalize-space(text())='${text}']`)), 10_000)

  const ratingButton = (label: string) => By.css(`[aria-label="${label}"]`)

  const pathname = () => driver.executeScript<string>('return window.location.pathname')

  const submittedIds = () =>
    driver.executeScript<string | null>(
      "return window.sessionStorage.getItem('feedback.submitted')",
    )

  const sendFirstTurn = async () => {
    await driver.get(`${server.origin}/bedrock-chat/ui`)
    await byText('GEMM tuning')

    const composer = await driver.findElement(By.css('textarea'))
    await composer.sendKeys('analyse job 42', Key.ENTER)
    await byText('Here is the analysis.')
  }

  before(async () => {
    driver = await buildChromeDriver()
  })

  after(async () => {
    await driver?.quit()
  })

  beforeEach(async () => {
    server = await startChatServer()
    server.on('conversation_list', () => {
      server.send({ type: 'conversation_list', conversations: ROSTER })
    })
    server.on('conversation_load', (frame) => {
      server.send({
        type: 'conversation_loaded',
        conversation_id: frame.conversation_id,
        conversation: {},
        messages: [],
      })
    })
    server.on('conversation_new', () => undefined)
    server.on('chat', () => {
      server.send({ type: 'conversation_created', conversation_id: 'conv-3' })
      server.send({
        type: 'conversation_titled',
        conversation_id: 'conv-3',
        title: 'Job 42 analysis',
      })
      server.send({
        type: 'ai_response',
        message: 'Here is the analysis.',
        message_id: MESSAGE_ID,
        tool_calls: [],
        tool_results: [],
        conversation_id: 'conv-3',
        metadata: {
          model_id: 'claude',
          model_name: 'Claude',
          tool_call_rounds: 0,
          total_tool_calls: 0,
          preprocessing_applied: false,
        },
      })
    })
  })

  afterEach(async () => {
    await server.close()
  })

  it('submits a correction from the keyboard and keeps the submitted state across a switch', async () => {
    server.on('feedback', (frame) => {
      server.send({
        type: 'feedback_ack',
        message_id: String(frame.message_id),
        feedback_id: 'fb-1',
        status: 'recorded',
      })
    })

    await sendFirstTurn()

    // FR-FB-005 / NFR-A11Y-002: the control opens from the keyboard and hands focus to the first field.
    const negative = await driver.findElement(ratingButton('Rate response unhelpful'))
    await negative.sendKeys(Key.ENTER)
    await driver.wait(async () => (await negative.getAttribute('aria-expanded')) === 'true', 10_000)
    expect(
      await driver.executeScript<string>('return document.activeElement.tagName'),
    ).to.equal('TEXTAREA')

    const correction = await driver.findElement(
      By.xpath(
        "//label[normalize-space()='What should the correct answer be? (optional)']" +
          '/following-sibling::textarea',
      ),
    )
    await correction.sendKeys(CORRECTION)
    await (
      await driver.findElement(By.xpath("//button[normalize-space()='Submit Feedback']"))
    ).click()

    // FR-FB-002b: the blank comment is omitted rather than sent as an empty string.
    await driver.wait(async () => server.sentOf('feedback').length === 1, 10_000)
    expect(server.sentOf('feedback')[0]).to.deep.equal({
      type: 'feedback',
      message_id: MESSAGE_ID,
      rating: 'negative',
      correction_text: CORRECTION,
    })

    // FR-FB-003: the controls are replaced by the submitted status region.
    await byText('✓ Feedback submitted')
    expect(await driver.findElements(ratingButton('Rate response helpful'))).to.have.length(0)

    // FR-FB-004: the id is persisted for the session, not held in component state.
    expect(JSON.parse((await submittedIds()) ?? '[]')).to.deep.equal([MESSAGE_ID])

    // FR-FB-004a: switching away and back leaves the rating in place, never a blank control.
    await (await driver.findElement(By.xpath("//button[normalize-space()='Stream triad']"))).click()
    await driver.wait(async () => (await pathname()) === '/bedrock-chat/ui/c/conv-2', 10_000)
    await (
      await driver.findElement(By.xpath("//button[normalize-space()='Job 42 analysis']"))
    ).click()
    await driver.wait(async () => (await pathname()) === '/bedrock-chat/ui/c/conv-3', 10_000)

    await byText('✓ Feedback submitted')
    expect(await driver.findElements(ratingButton('Rate response unhelpful'))).to.have.length(0)

    await assertNoAxeViolations(driver)
    await matchScreenshot(driver, 'e9-feedback-submitted')
  })

  it('reverts an optimistic rating when the server rejects it', async () => {
    server.on('feedback', (frame) => {
      server.send({
        type: 'feedback_error',
        code: 'unauthorized_feedback',
        message: 'principal may not rate this message',
        message_id: String(frame.message_id),
      })
    })

    await sendFirstTurn()

    await (await driver.findElement(ratingButton('Rate response helpful'))).click()
    await driver.wait(async () => server.sentOf('feedback').length === 1, 10_000)

    // FR-FB-003b / FR-FB-009: the server code decides the copy, not its message.
    const alert = await driver.wait(until.elementLocated(By.css('[role=alert]')), 10_000)
    expect(await alert.getText()).to.equal('You are not allowed to rate this message.')

    const positive = await driver.findElement(ratingButton('Rate response helpful'))
    expect(await positive.isEnabled()).to.equal(true)
    expect(await positive.getAttribute('aria-describedby')).to.equal(`feedback-error-${MESSAGE_ID}`)
    expect(JSON.parse((await submittedIds()) ?? '[]')).to.deep.equal([])

    await assertNoAxeViolations(driver)
  })
})
