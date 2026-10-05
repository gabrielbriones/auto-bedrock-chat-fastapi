import { afterAll, afterEach, beforeAll, describe, expect, it } from '@jest/globals'
import { HttpResponse, http } from 'msw'

import { HttpClient } from '@/shared/http/http-client'
import { isErr, isOk } from '@/shared/kernel/result'
import type { FileIngestRequest, WebCrawlRequest } from '@/domains/knowledge/domain/public'
import { HttpKbSourcesGateway } from '@/domains/knowledge/infrastructure/http-kb-sources.gateway'

import { server } from '../../../msw/server'

const ADMIN = 'http://localhost/chat/admin'
const silentLogger = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} }
const gateway = new HttpKbSourcesGateway(ADMIN, new HttpClient(), silentLogger)
const requests: { readonly method: string; readonly url: string }[] = []

const webRequest: WebCrawlRequest = {
  name: 'intel docs/2026',
  urls: ['https://example.com'],
  topic: null,
  maxDepth: 2,
  maxPages: null,
  allowedDomains: null,
  excludePatterns: null,
  ingestLinkedFiles: true,
  synthesize: true,
  headers: null,
  cookies: null,
}

const fileRequest: FileIngestRequest = {
  name: 'notes',
  topic: 'ops',
  files: [new File(['hello'], 'notes.txt', { type: 'text/plain' })],
  synthesize: true,
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
  server.events.on('request:start', ({ request }) => {
    requests.push({ method: request.method, url: request.url })
  })
})
afterEach(() => {
  server.resetHandlers()
  requests.length = 0
})
afterAll(() => server.close())

const last = () => requests[requests.length - 1]

describe('HttpKbSourcesGateway reads', () => {
  it('lists sources and reads the run status', async () => {
    const list = await gateway.listSources(AbortSignal.timeout(1_000))
    expect(isOk(list) && list.value).toEqual([{ source: 'feedback', count: 4 }, { source: 'intel-docs', count: 12 }])
    expect(last()).toEqual({ method: 'GET', url: `${ADMIN}/kb/sources` })

    const status = await gateway.status(AbortSignal.timeout(1_000))
    expect(isOk(status) && status.value.phase).toBe('idle')
    expect(last()).toEqual({ method: 'GET', url: `${ADMIN}/kb/sources/status` })
  })

  it('reports an aborted read with the dedicated Problem code', async () => {
    const controller = new AbortController()
    const pending = gateway.listSources(controller.signal)
    controller.abort()

    const result = await pending

    expect(isErr(result) && result.error.code).toBe('aborted')
  })
})

describe('HttpKbSourcesGateway web crawls', () => {
  it('POSTs the JSON body with the name and maps the 202 run', async () => {
    let body: unknown
    server.use(
      http.post(`${ADMIN}/kb/sources/web`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ run_id: 'run-9', phase: 'running', source_type: 'web' }, { status: 202 })
      }),
    )

    const result = await gateway.startWebCrawl(webRequest)

    expect(body).toEqual({ name: 'intel docs/2026', urls: ['https://example.com'], max_depth: 2, ingest_linked_files: true, synthesize: true })
    expect(isOk(result) && result.value).toMatchObject({ runId: 'run-9', phase: 'running' })
  })

  it('PUTs an override to the encoded name path without the name in the body', async () => {
    let body: unknown
    server.use(
      http.put(`${ADMIN}/kb/sources/web/:name`, async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ run_id: 'run-10', phase: 'running' }, { status: 202 })
      }),
    )

    const result = await gateway.overrideWebCrawl(webRequest)

    expect(last()).toEqual({ method: 'PUT', url: `${ADMIN}/kb/sources/web/intel%20docs%2F2026` })
    expect(body).toEqual({ urls: ['https://example.com'], max_depth: 2, ingest_linked_files: true, synthesize: true })
    expect(isOk(result) && result.value.runId).toBe('run-10')
  })

  it('surfaces the server code of a 409 so the store can tell a duplicate from a busy lock', async () => {
    server.use(
      http.post(`${ADMIN}/kb/sources/web`, () =>
        HttpResponse.json(
          { code: 'source_already_exists', detail: "source 'intel docs/2026' already has 4 document(s); use PUT to override" },
          { status: 409 },
        ),
      ),
    )

    const result = await gateway.startWebCrawl(webRequest)

    expect(isErr(result) && result.error).toMatchObject({
      status: 409,
      serverCode: 'source_already_exists',
      detail: expect.stringContaining('use PUT to override'),
    })
  })
})

describe('HttpKbSourcesGateway file ingestion', () => {
  it('POSTs multipart form data carrying name, topic, synthesize and files', async () => {
    let received: FormData | undefined
    server.use(
      http.post(`${ADMIN}/kb/sources/file`, async ({ request }) => {
        received = await request.formData()
        return HttpResponse.json({ run_id: 'run-11', phase: 'running', source_type: 'file' }, { status: 202 })
      }),
    )

    const result = await gateway.startFileIngest(fileRequest)

    expect(received?.get('name')).toBe('notes')
    expect(received?.get('topic')).toBe('ops')
    expect(received?.get('synthesize')).toBe('true')
    expect((received?.get('files') as File).name).toBe('notes.txt')
    expect(isOk(result) && result.value.sourceType).toBe('file')
  })

  it('PUTs an override with the name in the path only', async () => {
    let received: FormData | undefined
    server.use(
      http.put(`${ADMIN}/kb/sources/file/:name`, async ({ request }) => {
        received = await request.formData()
        return HttpResponse.json({ run_id: 'run-12', phase: 'running' }, { status: 202 })
      }),
    )

    await gateway.overrideFileIngest(fileRequest)

    expect(last()).toEqual({ method: 'PUT', url: `${ADMIN}/kb/sources/file/notes` })
    expect(received?.has('name')).toBe(false)
    expect(received?.get('synthesize')).toBe('true')
    expect(received?.getAll('files')).toHaveLength(1)
  })
})

describe('HttpKbSourcesGateway delete', () => {
  it('encodes the source name as a query parameter and maps the deletion count', async () => {
    const result = await gateway.deleteSource('intel docs/2026')

    expect(last()).toEqual({ method: 'DELETE', url: `${ADMIN}/kb/sources?name=intel%20docs%2F2026` })
    expect(isOk(result) && result.value).toEqual({ source: 'intel docs/2026', deleted: 4 })
  })
})
