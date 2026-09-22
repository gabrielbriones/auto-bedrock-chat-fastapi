import { expect } from 'chai'
import { By, Key, until, type WebDriver } from 'selenium-webdriver'

import { startChatServer, conversationSummary, type ChatServer } from './helpers/chat-server.js'
import { buildChromeDriver } from './helpers/webdriver.js'

const ROSTER = [
  conversationSummary('conv-1', 'GEMM tuning', 1),
  conversationSummary('conv-2', 'Stream triad', 30),
]

// E5 (STD-002 §3.4): create → send → rename → switch → delete, asserting the roster and the URL at
// each step. Drives the built bundle against a scripted backend, so the whole wire contract of
// SPEC-011 is exercised rather than mocked away at the gateway.
describe('E5 — conversation lifecycle', function () {
  this.timeout(60_000)

  let driver: WebDriver
  let server: ChatServer

  const byText = async (text: string) =>
    driver.wait(until.elementLocated(By.xpath(`//*[normalize-space(text())='${text}']`)), 10_000)

  // A dismissed Base UI menu stays mounted, so the most recently opened match is the live one.
  const clickMenuItem = async (label: string) => {
    const selector = By.xpath(`//*[@role='menuitem'][normalize-space()='${label}']`)
    await driver.wait(until.elementLocated(selector), 10_000)

    const items = await driver.findElements(selector)
    const visible: typeof items = []

    for (const item of items) {
      if (await item.isDisplayed()) {
        visible.push(item)
      }
    }

    const target = visible.at(-1) ?? items.at(-1)
    await target?.click()
  }

  const dialogText = async () => {
    const dialog = await driver.wait(
      until.elementLocated(By.css('[role=dialog], [role=alertdialog]')),
      10_000,
    )
    return dialog.getText()
  }

  const clickDialogButton = async (label: string) => {
    const button = await driver.wait(
      until.elementLocated(
        By.xpath(`//*[@role='dialog' or @role='alertdialog']//button[normalize-space()='${label}']`),
      ),
      10_000,
    )
    await driver.wait(until.elementIsVisible(button), 5_000)
    await button.click()
  }

  const pathname = () =>
    driver.executeScript<string>('return window.location.pathname')

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
        messages:
          frame.conversation_id === 'conv-2'
            ? [
                {
                  message_id: null,
                  role: 'assistant',
                  content: 'The measured bandwidth was 118 GB/s.',
                  timestamp: null,
                  tool_calls: [],
                  tool_results: [],
                  metadata: {},
                },
              ]
            : [],
      })
    })
    server.on('conversation_rename', (frame) => {
      server.send({
        type: 'conversation_renamed',
        conversation_id: frame.conversation_id,
        title: frame.title,
      })
    })
    server.on('conversation_delete', (frame) => {
      server.send({ type: 'conversation_deleted', conversation_id: frame.conversation_id })
    })
    server.on('conversation_new', () => undefined)
    server.on('chat', () => {
      server.send({ type: 'conversation_created', conversation_id: 'conv-3' })
      server.send({
        type: 'ai_response',
        message: 'Here is the analysis.',
        message_id: 'm-1',
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

  it('creates, sends, renames, switches and deletes, keeping the URL in step', async () => {
    await driver.get(`${server.origin}/bedrock-chat/ui`)
    await byText('GEMM tuning')

    // FR-CONV-002: a new conversation has no id yet, so it lives at /ui.
    await (await driver.findElement(By.xpath("//button[normalize-space()='New chat']"))).click()
    expect(server.sentOf('conversation_new')).to.have.length(1)
    expect(await pathname()).to.equal('/bedrock-chat/ui')

    // FR-CONV-010 / FR-CONV-011: the id the first turn produces becomes the address.
    const composer = await driver.findElement(By.css('textarea'))
    await composer.sendKeys('analyse job 42', Key.ENTER)
    await byText('Here is the analysis.')
    await driver.wait(async () => (await pathname()) === '/bedrock-chat/ui/c/conv-3', 10_000)

    // FR-CONV-004 / FIX-15: a styled dialog, never window.prompt.
    await (await driver.findElement(By.css('[aria-label="Options for GEMM tuning"]'))).click()
    await clickMenuItem('Rename')
    const titleField = await driver.wait(until.elementLocated(By.css('[role=dialog] input')), 5_000)
    await titleField.clear()
    await titleField.sendKeys('GEMM tuning v2', Key.ENTER)
    await byText('GEMM tuning v2')
    expect(server.sentOf('conversation_rename')[0]?.title).to.equal('GEMM tuning v2')

    // FR-CONV-003 / FR-CONV-011.
    await (await driver.findElement(By.xpath("//button[normalize-space()='Stream triad']"))).click()
    await driver.wait(async () => (await pathname()) === '/bedrock-chat/ui/c/conv-2', 10_000)
    await byText('The measured bandwidth was 118 GB/s.')
    expect(await driver.findElements(By.xpath("//article[normalize-space(.)='Here is the analysis.']")))
      .to.have.length(0)

    // A response already in flight for the previous thread must not roll the route or transcript
    // back. The following rename is an ordered marker proving the browser consumed both frames.
    server.send({
      type: 'conversation_loaded',
      conversation_id: 'conv-3',
      conversation: {},
      messages: [
        {
          message_id: null,
          role: 'assistant',
          content: 'Stale response from the previous conversation.',
          timestamp: null,
          tool_calls: [],
          tool_results: [],
          metadata: {},
        },
      ],
    })
    server.send({
      type: 'conversation_renamed',
      conversation_id: 'conv-1',
      title: 'GEMM tuning settled',
    })
    await byText('GEMM tuning settled')
    expect(await pathname()).to.equal('/bedrock-chat/ui/c/conv-2')
    expect(
      await driver.findElements(
        By.xpath("//article[normalize-space(.)='Stale response from the previous conversation.']"),
      ),
    ).to.have.length(0)

    // FR-CONV-005 / FIX-15: a confirmation naming the conversation, never window.confirm.
    await (await driver.findElement(By.css('[aria-label="Options for Stream triad"]'))).click()
    await clickMenuItem('Delete')
    expect(await dialogText()).to.contain('Delete "Stream triad"? This cannot be undone.')
    await clickDialogButton('Delete')

    expect(server.sentOf('conversation_delete')[0]?.conversation_id).to.equal('conv-2')
    // FR-CONV-016 / FIX-16: the list is refetched, never patched in place.
    await driver.wait(async () => server.sentOf('conversation_list').length >= 2, 10_000)
  })
})
