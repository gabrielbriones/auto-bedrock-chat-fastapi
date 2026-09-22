import { readFile } from 'node:fs/promises'
import { createServer, type Server } from 'node:http'
import path from 'node:path'

import { expect } from 'chai'
import { By, until, type WebDriver } from 'selenium-webdriver'

import { buildChromeDriver } from './helpers/webdriver.js'

const contentType = (filePath: string): string => {
  if (filePath.endsWith('.js')) return 'text/javascript'
  if (filePath.endsWith('.css')) return 'text/css'
  if (filePath.endsWith('.json')) return 'application/json'
  return 'text/html'
}

describe('Admin capability guard', function () {
  this.timeout(60_000)

  let driver: WebDriver
  let server: Server
  let origin: string
  let capabilityRequests = 0
  let protectedAdminRequests = 0

  before(async () => {
    const dist = path.resolve(process.cwd(), 'dist')
    const bootstrap = await readFile(
      path.resolve(process.cwd(), 'tests/msw/fixtures/bootstrap-config.json'),
    )

    server = createServer(async (request, response) => {
      const requestPath = new URL(request.url ?? '/', 'http://localhost').pathname

      if (requestPath === '/bedrock-chat/config') {
        response.writeHead(200, { 'content-type': 'application/json' })
        response.end(bootstrap)
        return
      }

      if (requestPath === '/bedrock-chat/admin/_capabilities') {
        capabilityRequests += 1
        response.writeHead(200, { 'content-type': 'application/json' })
        response.end(JSON.stringify({
          is_admin: false,
          anonymous: false,
          token_usage_enabled: false,
        }))
        return
      }

      if (requestPath.startsWith('/bedrock-chat/admin/')) {
        protectedAdminRequests += 1
        response.writeHead(403)
        response.end()
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
    origin = `http://127.0.0.1:${address.port}`
    driver = await buildChromeDriver()
  })

  after(async () => {
    await driver?.quit()
    await new Promise<void>((resolve, reject) => {
      server.close((error) => error === undefined ? resolve() : reject(error))
    })
  })

  it('denies before an admin chunk or protected admin request is fetched', async () => {
    await driver.get(`${origin}/bedrock-chat/dashboard/feedback`)
    const heading = await driver.wait(until.elementLocated(By.css('h1')), 10_000)

    expect(await heading.getText()).to.equal('Access denied')

    const resources = await driver.executeScript<string[]>(
      "return performance.getEntriesByType('resource').map((entry) => entry.name)",
    )
    expect(resources.some((resource) => /\/ui\/assets\/admin\./.test(resource))).to.equal(false)
    expect(capabilityRequests).to.equal(1)
    expect(protectedAdminRequests).to.equal(0)
  })
})