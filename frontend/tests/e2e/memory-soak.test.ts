import { expect } from 'chai'
import { By, Key, until, type WebDriver } from 'selenium-webdriver'

import { startChatServer, type ChatServer } from './helpers/chat-server.js'
import { buildChromeDriver } from './helpers/webdriver.js'
import { assertBoundedGrowth, type HeapSample } from './helpers/memory-soak.js'

// NFR-PERF-009: 30 minutes by default — real enough to catch a leak that only shows up over a
// long session, configurable so the harness itself can be smoke-tested in seconds during
// development. `npm run test:e2e:soak` runs this with the real default; `npm run test:e2e` (the
// normal suite) skips it entirely so every other run stays fast.
const DURATION_MS = Number(process.env.SOAK_DURATION_MS ?? 30 * 60 * 1000)
const SAMPLE_INTERVAL_MS = Number(process.env.SOAK_SAMPLE_INTERVAL_MS ?? 15_000)
// Stated heap-growth ceiling (Phase 2 accept criterion): a chat session doing nothing but
// streaming text should not accumulate more than a few MB of retained turns/observers once GC'd.
const CEILING_MB = Number(process.env.SOAK_CEILING_MB ?? 20)
const WARMUP_MS = Math.min(2 * 60 * 1000, Math.floor(DURATION_MS / 4))

describe('NFR-PERF-009 — 30-minute streaming memory soak', function () {
  this.timeout(DURATION_MS + 5 * 60_000)

  let driver: WebDriver
  let server: ChatServer

  before(function () {
    // Opt-in only: `RUN_SOAK=1 npm run test:e2e:soak`. A real run takes 30 minutes by design and
    // must not slow down the default `npm run test:e2e` suite.
    if (process.env.RUN_SOAK !== '1') {
      this.skip()
    }
  })

  before(async () => {
    // --expose-gc lets the sampler force a collection before each reading, so growth reflects
    // retained memory rather than whatever GC happened not to have run yet.
    driver = await buildChromeDriver(['--js-flags=--expose-gc', '--enable-precise-memory-info'])
  })

  after(async () => {
    await driver?.quit()
  })

  beforeEach(async () => {
    server = await startChatServer()
    let turn = 0
    let conversationCreated = false

    server.on('chat', () => {
      if (!conversationCreated) {
        server.send({ type: 'conversation_created', conversation_id: 'soak-conversation' })
        conversationCreated = true
      }

      turn += 1
      server.send({ type: 'typing', message: `Turn ${turn} in progress` })
      server.send({
        type: 'ai_response',
        message: `Turn ${turn} complete: the workload is compute-bound.`,
        message_id: `soak-m-${turn}`,
        tool_calls: [],
        tool_results: [],
        conversation_id: 'soak-conversation',
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

  it('keeps heap growth under the stated ceiling across continuous streaming', async () => {
    await driver.get(`${server.origin}/bedrock-chat/ui/`)

    const hasHeapApi = await driver.executeScript<boolean>(
      'return typeof performance !== "undefined" && typeof performance.memory !== "undefined"',
    )

    if (!hasHeapApi) {
      throw new Error('performance.memory is unavailable; this soak requires headless Chrome')
    }

    const readyComposer = () =>
      driver.wait(until.elementLocated(By.css('textarea[aria-label="Message"]:not([disabled])')), 20_000)

    await readyComposer()

    const samples: HeapSample[] = []
    const start = Date.now()
    const endAt = start + DURATION_MS
    let turn = 0

    while (Date.now() < endAt) {
      const intervalEnd = Math.min(Date.now() + SAMPLE_INTERVAL_MS, endAt)

      // Keeps a turn streaming through essentially the whole window, not just at the sample
      // boundaries — a long-lived session with continuous activity is the case NFR-PERF-009 cares
      // about, not an idle tab.
      while (Date.now() < intervalEnd) {
        const composer = await readyComposer()
        turn += 1
        await composer.sendKeys(`Soak turn ${turn}`, Key.ENTER)
        await driver.wait(() => server.sentOf('chat').length === turn, 15_000)
      }

      await driver.executeScript('if (typeof window.gc === "function") { window.gc() }')
      const bytes = await driver.executeScript<number>('return performance.memory.usedJSHeapSize')
      samples.push({ atMs: Date.now() - start, bytes })
    }

    const warmupSamples = Math.max(1, Math.round(WARMUP_MS / SAMPLE_INTERVAL_MS))

    expect(() =>
      assertBoundedGrowth(samples, { warmupSamples, ceilingBytes: CEILING_MB * 1_048_576 }),
    ).not.to.throw()
  })
})
