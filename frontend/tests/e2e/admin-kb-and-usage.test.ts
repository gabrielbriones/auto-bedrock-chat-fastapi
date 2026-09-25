import { readFile } from 'node:fs/promises'
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import path from 'node:path'

import { expect } from '@jest/globals'
import { By, Key, until, type WebDriver, type WebElement } from 'selenium-webdriver'

import { assertNoAxeViolations } from './helpers/axe.js'
import { buildChromeDriver } from './helpers/webdriver.js'

const KB_ID = 'kb/2026/perf-guide'
const KB_CONTENT = 'Use the vector path.'
const UPDATED_KB_CONTENT = 'Use the vector path for AVX-512.'

type ByUserRequest = {
  readonly userId: string
  readonly offset: number
  readonly limit: number
}

type AdminTestServer = {
  readonly origin: string
  readonly knowledgeQueries: string[]
  readonly patchBodies: Record<string, unknown>[]
  readonly byDayQueries: string[]
  readonly byUserRequests: ByUserRequest[]
  close(): Promise<void>
}

const documentWire = (overrides: Record<string, unknown> = {}): Record<string, unknown> => ({
  id: KB_ID,
  content: KB_CONTENT,
  title: 'Performance guide',
  source: 'feedback',
  source_url: null,
  topic: 'compute',
  date_published: '2026-09-01',
  metadata: { owner: 'perf' },
  tags: ['ipc', 'vector'],
  chunk_count: 4,
  created_at: '2026-09-01T12:00:00Z',
  credibility_score: 0.8,
  removal_flagged: true,
  ...overrides,
})

const documentPage = (): Record<string, unknown> => ({
  items: [documentWire()],
  total: 1,
  limit: 50,
  offset: 0,
})

const readBody = async (request: IncomingMessage): Promise<string> => {
  const chunks: Buffer[] = []
  for await (const chunk of request) {
    chunks.push(Buffer.from(chunk))
  }
  return Buffer.concat(chunks).toString('utf8')
}

const contentType = (filePath: string): string => {
  if (filePath.endsWith('.js')) return 'text/javascript'
  if (filePath.endsWith('.css')) return 'text/css'
  if (filePath.endsWith('.json') || filePath.endsWith('.map')) return 'application/json'
  return 'text/html'
}

const sendJson = (response: ServerResponse<IncomingMessage>, value: unknown, status = 200): void => {
  response.writeHead(status, { 'content-type': 'application/json' })
  response.end(JSON.stringify(value))
}

const startAdminTestServer = async (): Promise<AdminTestServer> => {
  const dist = path.resolve(process.cwd(), 'dist')
  const baseConfig = JSON.parse(
    await readFile(path.resolve(process.cwd(), 'tests/msw/fixtures/bootstrap-config.json'), 'utf8'),
  ) as Record<string, unknown>
  const knowledgeQueries: string[] = []
  const patchBodies: Record<string, unknown>[] = []
  const byDayQueries: string[] = []
  const byUserRequests: ByUserRequest[] = []
  let currentDocument = documentWire()

  const server = createServer(async (request, response) => {
    const requestUrl = new URL(request.url ?? '/', `http://${request.headers.host ?? 'localhost'}`)
    const requestPath = requestUrl.pathname
    const adminPath = requestPath.startsWith('/bedrock-chat/admin')
      ? requestPath.slice('/bedrock-chat/admin'.length)
      : null

    if (requestPath === '/bedrock-chat/config') {
      const address = server.address()
      const port = address !== null && typeof address !== 'string' ? address.port : 0
      sendJson(response, {
        ...baseConfig,
        authEnabled: false,
        adminEnabled: true,
        websocketUrl: `ws://127.0.0.1:${port}/bedrock-chat/ws`,
      })
      return
    }

    if (requestPath === '/bedrock-chat/admin/_capabilities') {
      sendJson(response, {
        is_admin: true,
        anonymous: false,
        token_usage_enabled: true,
      })
      return
    }

    if (adminPath === '/kb/documents' && request.method === 'GET') {
      knowledgeQueries.push(requestUrl.search)
      sendJson(response, documentPage())
      return
    }

    if (adminPath?.startsWith('/kb/documents/') === true && request.method === 'GET') {
      sendJson(response, currentDocument)
      return
    }

    if (adminPath?.startsWith('/kb/documents/') === true && request.method === 'PATCH') {
      const patch = JSON.parse(await readBody(request)) as Record<string, unknown>
      patchBodies.push(patch)
      currentDocument = {
        ...currentDocument,
        ...(patch.topic === undefined ? {} : { topic: patch.topic }),
        ...(patch.content === undefined ? {} : { content: patch.content }),
      }
      sendJson(response, currentDocument)
      return
    }

    if (adminPath === '/tokens/summary' && request.method === 'GET') {
      sendJson(response, {
        items: [{ model_id: 'claude-3', input_tokens: 120, output_tokens: 30, turn_count: 4 }],
      })
      return
    }

    if (adminPath === '/tokens/top-users' && request.method === 'GET') {
      sendJson(response, {
        items: [{ user_id: 'alice@example.com', input_tokens: 100, output_tokens: 50 }],
      })
      return
    }

    if (adminPath === '/tokens/by-day' && request.method === 'GET') {
      byDayQueries.push(requestUrl.search)
      sendJson(response, {
        items: [{ date: '2026-05-15', input_tokens: 80, output_tokens: 20, turn_count: 3 }],
      })
      return
    }

    if (adminPath === '/tokens/by-user' && request.method === 'GET') {
      const userId = requestUrl.searchParams.get('user_id') ?? ''
      const offset = Number(requestUrl.searchParams.get('offset') ?? '0')
      const limit = Number(requestUrl.searchParams.get('limit') ?? '50')
      byUserRequests.push({ userId, offset, limit })
      const items = Array.from({ length: offset === 0 ? limit : 1 }, (_, index) => ({
        session_id: `session-${offset + index}`,
        model_id: 'claude-3',
        input_tokens: 2,
        output_tokens: 3,
        turn_ts: '2026-05-15T12:00:00.000Z',
      }))
      sendJson(response, { user_id: userId, items })
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

  return {
    origin: `http://127.0.0.1:${address.port}`,
    knowledgeQueries,
    patchBodies,
    byDayQueries,
    byUserRequests,
    async close() {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error === undefined ? resolve() : reject(error)))
      })
    },
  }
}

describe('E14/E15 — admin knowledge base and usage analytics', function () {

  let driver: WebDriver
  let server: AdminTestServer

  const byText = async (text: string) =>
    driver.wait(until.elementLocated(By.xpath(`//*[normalize-space(text())='${text}']`)), 10_000)

  const dateInput = async (index: 0 | 1): Promise<WebElement> => {
    const inputs = await driver.wait(async () => {
      const elements = await driver.findElements(By.css('input[type="date"]'))
      return elements.length >= 2 ? elements : false
    }, 10_000)
    if (inputs === false) throw new Error('date inputs were not rendered')
    const input = inputs[index]
    if (input === undefined) throw new Error(`date input ${index} was not rendered`)
    return input
  }

  const setDate = async (label: string, value: string): Promise<void> => {
    const input = await dateInput(label === 'Start date' ? 0 : 1)
    await driver.executeScript(
      `const input = arguments[0];
       const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
       setter.call(input, arguments[1]);
       input.dispatchEvent(new Event('input', { bubbles: true }));
       input.dispatchEvent(new Event('change', { bubbles: true }));`,
      input,
      value,
    )
    const index = label === 'Start date' ? 0 : 1
    await driver.wait(async () => (await (await dateInput(index)).getAttribute('value')) === value, 10_000)
  }

  beforeAll(async () => {
    driver = await buildChromeDriver()
  })

  afterAll(async () => {
    await driver?.quit()
  })

  beforeEach(async () => {
    server = await startAdminTestServer()
  })

  afterEach(async () => {
    await server.close()
  })

  it('E14 — filters flagged KB documents and confirms the sparse re-embed patch', async () => {
    await driver.get(`${server.origin}/bedrock-chat/dashboard/kb-browser`)
    await byText('Knowledge base')

    await (await driver.findElement(By.css('[role="checkbox"]'))).click()
    await driver.wait(
      async () => server.knowledgeQueries.some((query) => query.includes('removal_flagged=true')),
      10_000,
    )

    await (await driver.findElement(By.css('button[aria-label="Open Performance guide"]'))).click()
    const drawer = await driver.wait(
      until.elementLocated(By.css('[data-slot="sheet-content"]')),
      10_000,
    )
    const topic = await drawer.findElement(
      By.xpath(".//label[normalize-space()='Topic']/following-sibling::input"),
    )
    const content = await drawer.findElement(
      By.xpath(".//label[normalize-space()='Content']/following-sibling::textarea"),
    )
    await topic.clear()
    await topic.sendKeys('vectorization')
    await content.clear()
    await content.sendKeys(UPDATED_KB_CONTENT)
    await (await drawer.findElement(By.css('form button[type="submit"]'))).click()

    await byText('This will re-embed the document')
    expect(server.patchBodies).toHaveLength(0)
    await (
      await driver.findElement(
        By.xpath("//*[@role='dialog']//button[normalize-space()='Save and re-embed']"),
      )
    ).click()
    await driver.wait(async () => server.patchBodies.length === 1, 10_000)

    expect(server.patchBodies[0]).toEqual({
      topic: 'vectorization',
      content: UPDATED_KB_CONTENT,
    })
    await byText('Document saved.')
    await assertNoAxeViolations(driver)
  })

  it('E15 — validates a usage range, restores it from the URL, and paginates by user', async () => {
    await driver.get(`${server.origin}/bedrock-chat/dashboard/token-usages`)
    await byText('Usage')

    await setDate('Start date', '2026-05-31')
    await setDate('End date', '2026-05-01')
    await (
      await driver.findElement(
        By.xpath("//div[@data-slot='card'][.//h2[normalize-space()='By day']]//button[normalize-space()='Apply']"),
      )
    ).click()
    await byText('End date must be after start date.')
    expect(server.byDayQueries).toHaveLength(0)

    await setDate('Start date', '2026-05-01')
    await setDate('End date', '2026-05-31')
    await (
      await driver.findElement(
        By.xpath("//div[@data-slot='card'][.//h2[normalize-space()='By day']]//button[normalize-space()='Apply']"),
      )
    ).click()
    await driver.wait(async () => server.byDayQueries.length === 1, 10_000)
    expect(await driver.getCurrentUrl()).toContain('from=2026-05-01')
    expect(await driver.getCurrentUrl()).toContain('to=2026-05-31')

    await driver.navigate().refresh()
    const start = await dateInput(0)
    const end = await dateInput(1)
    await driver.wait(async () => (await start.getAttribute('value')) === '2026-05-01', 10_000)
    expect(await end.getAttribute('value')).toBe('2026-05-31')

    const user = await driver.findElement(
      By.xpath("//label[normalize-space()='User ID']/following-sibling::input"),
    )
    await user.sendKeys('alice@example.com', Key.ENTER)
    await driver.wait(async () => server.byUserRequests.length === 1, 10_000)
    expect(server.byUserRequests[0]).toMatchObject({
      userId: 'alice@example.com',
      offset: 0,
      limit: 50,
    })

    const nextLocator = By.xpath("//nav[@aria-label='User usage pagination']//button[normalize-space()='Next']")
    const next = await driver.findElement(nextLocator)
    expect(await next.isEnabled()).toBe(true)
    await next.click()
    await driver.wait(async () => server.byUserRequests.length === 2, 10_000)
    expect(server.byUserRequests[1]).toMatchObject({
      userId: 'alice@example.com',
      offset: 50,
      limit: 50,
    })
    const range = await driver.wait(
      until.elementLocated(By.xpath("//nav[@aria-label='User usage pagination']//p")),
      10_000,
    )
    expect(await range.getText()).toBe('Showing 51\u201351')
    const nextAfterPageChange = await driver.findElement(nextLocator)
    expect(await nextAfterPageChange.isEnabled()).toBe(false)
    await assertNoAxeViolations(driver)
  })
})