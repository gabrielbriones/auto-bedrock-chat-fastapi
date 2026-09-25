import path from 'node:path'

import { expect } from '@jest/globals'
import { By, Key, until, type WebDriver } from 'selenium-webdriver'

import { analyzeChatRouteBundle } from './helpers/manifest-graph.js'
import { startChatServer, type ChatServer } from './helpers/chat-server.js'
import { buildChromeDriver } from './helpers/webdriver.js'

// NFR-PERF-007 / FR-SHELL-015: check:chunks (scripts/check-chunk-split.mjs) proves the admin
// subtree and shiki stay out of the chat entry's *static* import graph. This proves the same
// thing from the other side — the built bundle actually running in a browser — by cross
// referencing the same manifest against every resource a real streamed chat turn fetches. It
// would not catch a chunk that is technically split but gets prefetched/loaded eagerly anyway;
// resource timing does.
describe('Chat route bundle separation (production build)', function () {

  let driver: WebDriver
  let server: ChatServer

  beforeAll(async () => {
    driver = await buildChromeDriver()
  })

  afterAll(async () => {
    await driver?.quit()
  })

  beforeEach(async () => {
    server = await startChatServer()
    server.on('chat', () => {
      server.send({ type: 'conversation_created', conversation_id: 'conv-1' })
      server.send({ type: 'typing', message: 'Looking' })
      server.send({ type: 'typing', message: 'Looking into the workload' })
      server.send({
        type: 'ai_response',
        message: 'The workload is compute-bound.',
        message_id: 'm-1',
        tool_calls: [],
        tool_results: [],
        conversation_id: 'conv-1',
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
    await server?.close()
  })

  it('never fetches an admin, recharts or shiki chunk during a streamed chat turn', async () => {
    const manifestPath = path.resolve(process.cwd(), 'dist/.vite/manifest.json')
    const { forbiddenFiles } = await analyzeChatRouteBundle(manifestPath)

    await driver.get(`${server.origin}/bedrock-chat/ui/`)

    const composer = await driver.wait(
      until.elementLocated(By.css('textarea[aria-label="Message"]:not([disabled])')),
      15_000,
    )
    await composer.sendKeys('How is this workload bound?', Key.ENTER)

    await driver.wait(() => server.sentOf('chat').length === 1, 10_000)

    let lastBody = ''
    try {
      await driver.wait(async () => {
        lastBody = await driver.executeScript<string>('return document.body.innerText')
        return lastBody.includes('compute-bound')
      }, 10_000)
    } catch (error) {
      throw new Error(`response text never arrived; body was: ${lastBody}`, { cause: error })
    }

    const loadedPaths = await driver.executeScript<string[]>(
      'return performance.getEntriesByType("resource").map((entry) => new URL(entry.name).pathname)',
    )
    const loadedFiles = new Set(loadedPaths.map((requestPath) => requestPath.replace(/^\/bedrock-chat\/ui\//, '')))

    // Sanity check: the analysis (and this assertion) is meaningless if the entry itself never
    // shows up as a loaded resource.
    expect([...loadedFiles].some((file) => /^assets\/index-.*\.js$/.test(file))).toBe(true)

    const violations = [...forbiddenFiles].filter((file) => loadedFiles.has(file))

    expect(violations).toEqual([])
  })
})
