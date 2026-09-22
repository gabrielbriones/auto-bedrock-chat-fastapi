import { expect } from 'chai'
import { By, until, type WebDriver } from 'selenium-webdriver'

import { startChatServer, conversationSummary, type ChatServer } from './helpers/chat-server.js'
import { buildChromeDriver } from './helpers/webdriver.js'

const ROSTER = [
  conversationSummary('conv-1', 'GEMM tuning', 1),
  conversationSummary('conv-2', 'Stream triad', 30),
  conversationSummary('conv-3', 'Pointer chase', 60),
]

// E6 (STD-002 §3.4): bulk delete where the server reports a subset. P5 / FR-CONV-006a is the whole
// point — the legacy client mutated its list in place and reported a partly-failed batch as a
// success, leaving skipped ids stuck selected.
describe('E6 — bulk delete with a partial server result', function () {
  this.timeout(60_000)

  let driver: WebDriver
  let server: ChatServer

  const byText = async (text: string) =>
    driver.wait(until.elementLocated(By.xpath(`//*[normalize-space(text())='${text}']`)), 10_000)

  before(async () => {
    driver = await buildChromeDriver()
  })

  after(async () => {
    await driver?.quit()
  })

  beforeEach(async () => {
    server = await startChatServer()
    let listRequests = 0

    server.on('conversation_list', () => {
      listRequests += 1
      // The refetch reflects only what the server actually deleted.
      server.send({
        type: 'conversation_list',
        conversations: listRequests === 1 ? ROSTER : ROSTER.slice(1),
      })
    })

    server.on('conversation_delete_bulk', () => {
      server.send({
        type: 'conversation_bulk_deleted',
        deleted_ids: ['conv-1'],
        active_conversation_deleted: false,
      })
    })
  })

  afterEach(async () => {
    await server.close()
  })

  it('reports the ids the server skipped and refetches instead of patching in place', async () => {
    await driver.get(`${server.origin}/bedrock-chat/ui`)
    await byText('GEMM tuning')

    // FR-CONV-006: the select-all control carries a real indeterminate state.
    const selectAll = await driver.findElement(By.css('[aria-label="Select all conversations"]'))
    await (await driver.findElement(By.css('[aria-label="Select GEMM tuning"]'))).click()
    expect(await selectAll.getAttribute('aria-checked')).to.equal('mixed')

    await selectAll.click()
    await byText('3 selected')
    expect(await selectAll.getAttribute('aria-checked')).to.equal('true')

    // FR-CONV-006: a guard before the destructive confirm, with the count in the question.
    await (await driver.findElement(By.xpath("//button[normalize-space()='Delete selected']"))).click()
    await byText('Delete 3 conversations?')
    const confirm = await driver.wait(
      until.elementLocated(
        By.xpath("//*[@role='dialog' or @role='alertdialog']//button[normalize-space()='Delete']"),
      ),
      10_000,
    )
    await confirm.click()

    expect(server.sentOf('conversation_delete_bulk')[0]?.conversation_ids).to.have.length(3)

    // P5: two of three survive, and the user is told rather than shown a success.
    await byText('2 conversations could not be deleted.')

    // FR-CONV-006a: the whole *requested* set leaves the selection, so nothing is stuck selected.
    await driver.wait(async () => {
      const bars = await driver.findElements(By.xpath("//button[normalize-space()='Delete selected']"))
      return bars.length === 0
    }, 10_000)

    // FR-CONV-016 / FIX-16: the surviving list comes from a refetch.
    await driver.wait(async () => server.sentOf('conversation_list').length >= 2, 10_000)
    await byText('Stream triad')
    expect(await driver.findElements(By.xpath("//button[normalize-space()='GEMM tuning']"))).to.have.length(0)
  })
})
