import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import path from 'node:path'

import { WebSocketServer, type WebSocket } from 'ws'

export type ClientFrame = Record<string, unknown> & { readonly type: string }

export type ChatServer = {
  readonly origin: string
  /** Every client frame the SPA has sent, in order. */
  readonly sent: readonly ClientFrame[]
  sentOf(type: string): readonly ClientFrame[]
  send(frame: Record<string, unknown>): void
  /** Replies are scripted per client frame type, so a journey reads as a conversation. */
  on(type: string, reply: (frame: ClientFrame) => void): void
  close(): Promise<void>
}

export type ChatServerOptions = {
  readonly config?: Readonly<Record<string, unknown>>
}

const TIMESTAMP = '2026-09-01T12:00:00Z'

const contentType = (filePath: string): string => {
  if (filePath.endsWith('.js')) return 'text/javascript'
  if (filePath.endsWith('.css')) return 'text/css'
  if (filePath.endsWith('.json')) return 'application/json'
  return 'text/html'
}

// The built bundle plus a scripted backend. Serving `dist` rather than the dev server is the point:
// these journeys exercise what actually ships (STD-002 §3.4).
export const startChatServer = async (options: ChatServerOptions = {}): Promise<ChatServer> => {
  const dist = path.resolve(process.cwd(), 'dist')
  const baseConfig = JSON.parse(
    await readFile(path.resolve(process.cwd(), 'tests/msw/fixtures/bootstrap-config.json'), 'utf8'),
  ) as Record<string, unknown>

  const sent: ClientFrame[] = []
  const replies = new Map<string, (frame: ClientFrame) => void>()
  let socket: WebSocket | null = null

  const send = (frame: Record<string, unknown>): void => {
    socket?.send(JSON.stringify({ timestamp: TIMESTAMP, ...frame }))
  }

  const server = createServer(async (request, response) => {
    const requestPath = new URL(request.url ?? '/', 'http://localhost').pathname
    const address = server.address()
    const port = address !== null && typeof address !== 'string' ? address.port : 0

    if (requestPath === '/bedrock-chat/config') {
      response.writeHead(200, { 'content-type': 'application/json' })
      response.end(
        JSON.stringify({
          ...baseConfig,
          ...options.config,
          websocketUrl: `ws://127.0.0.1:${port}/bedrock-chat/ws`,
          // FR-CONV-001: the roster only renders for an authenticated principal.
          ssoEnabled: true,
          ssoAuthenticated: true,
          ssoUserDisplay: 'E2E user',
        }),
      )
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

  const sockets = new WebSocketServer({ server, path: '/bedrock-chat/ws' })

  sockets.on('connection', (connection) => {
    socket = connection
    send({ type: 'connection_established', session_id: 'e2e-session', message: 'connected' })
    // P2: authentication is per socket, so every connection has to be authenticated again before
    // the roster is available (FR-CONV-001).
    send({
      type: 'auth_configured',
      message: 'Signed in',
      auth_type: 'sso',
      display_name: 'E2E user',
    })

    connection.on('message', (raw) => {
      const frame = JSON.parse(String(raw)) as ClientFrame
      sent.push(frame)
      replies.get(frame.type)?.(frame)
    })
  })

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()

  if (address === null || typeof address === 'string') {
    throw new Error('test server did not expose a TCP port')
  }

  return {
    origin: `http://127.0.0.1:${address.port}`,
    sent,
    sentOf: (type) => sent.filter((frame) => frame.type === type),
    send,
    on: (type, reply) => replies.set(type, reply),
    async close() {
      // An open websocket keeps the HTTP server listening, so the client is torn down first.
      socket?.terminate()
      socket = null
      await new Promise<void>((resolve) => sockets.close(() => resolve()))
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error === undefined ? resolve() : reject(error)))
      })
    },
  }
}

export const conversationSummary = (id: string, title: string, minutesAgo: number) => ({
  id,
  title,
  updated_at: new Date(Date.parse(TIMESTAMP) - minutesAgo * 60_000).toISOString(),
  message_count: 2,
})
