import { readFile } from 'node:fs/promises'
import { createServer, type ServerResponse } from 'node:http'
import path from 'node:path'

import { WebSocketServer, type WebSocket } from 'ws'

type ClientFrame = Record<string, unknown> & { readonly type: string }

export type AccessibilityServer = {
  readonly origin: string
  sentOf(type: string): readonly ClientFrame[]
  close(): Promise<void>
}

const TIMESTAMP = '2026-09-01T12:00:00Z'

const contentType = (filePath: string): string => {
  if (filePath.endsWith('.js')) return 'text/javascript'
  if (filePath.endsWith('.css')) return 'text/css'
  if (filePath.endsWith('.json') || filePath.endsWith('.map')) return 'application/json'
  return 'text/html'
}

const fixture = async (relativePath: string): Promise<unknown> =>
  JSON.parse(
    await readFile(path.resolve(process.cwd(), 'tests/msw/fixtures', relativePath), 'utf8'),
  )

export async function startAccessibilityServer(): Promise<AccessibilityServer> {
  const dist = path.resolve(process.cwd(), 'dist')
  const [
    baseConfig,
    feedbackList,
    feedbackEntry,
    feedbackStats,
    synthesisStatus,
    knowledgeList,
    knowledgeDocument,
    tokenSummary,
    tokenTopUsers,
    tokenByDay,
    tokenByUser,
    conversationList,
    conversationLoaded,
  ] = await Promise.all([
    fixture('bootstrap-config.json'),
    fixture('review/feedback-list.json'),
    fixture('review/feedback-entry.json'),
    fixture('review/feedback-stats.json'),
    fixture('review/synthesis-status.json'),
    fixture('knowledge/kb-document-list.json'),
    fixture('knowledge/kb-document.json'),
    fixture('telemetry/token-summary.json'),
    fixture('telemetry/token-top-users.json'),
    fixture('telemetry/token-by-day.json'),
    fixture('telemetry/token-by-user.json'),
    fixture('ws/conversation_list.json'),
    fixture('ws/conversation_loaded.json'),
  ])
  const sent: ClientFrame[] = []
  const connections = new Set<WebSocket>()

  const sendJson = (response: ServerResponse, value: unknown) => {
    response.writeHead(200, { 'content-type': 'application/json' })
    response.end(JSON.stringify(value))
  }

  const server = createServer(async (request, response) => {
    const requestUrl = new URL(request.url ?? '/', `http://${request.headers.host ?? 'localhost'}`)
    const requestPath = requestUrl.pathname
    const address = server.address()
    const port = address !== null && typeof address !== 'string' ? address.port : 0

    if (requestPath === '/bedrock-chat/config') {
      sendJson(response, {
        ...(baseConfig as Record<string, unknown>),
        adminEnabled: true,
        websocketUrl: `ws://127.0.0.1:${port}/bedrock-chat/ws`,
        ssoEnabled: true,
        ssoAuthenticated: true,
        ssoUserDisplay: 'Accessibility tester',
      })
      return
    }

    if (requestPath === '/bedrock-chat/admin/_capabilities') {
      sendJson(response, { is_admin: true, anonymous: false, token_usage_enabled: true })
      return
    }

    const adminPath = requestPath.startsWith('/bedrock-chat/admin')
      ? requestPath.slice('/bedrock-chat/admin'.length)
      : null

    if (adminPath === '/feedback/stats') return sendJson(response, feedbackStats)
    if (adminPath === '/feedback') return sendJson(response, feedbackList)
    if (adminPath?.startsWith('/feedback/') === true) return sendJson(response, feedbackEntry)
    if (adminPath === '/synthesis/status') return sendJson(response, synthesisStatus)
    if (adminPath === '/kb/documents') return sendJson(response, knowledgeList)
    if (adminPath?.startsWith('/kb/documents/') === true) return sendJson(response, knowledgeDocument)
    if (adminPath === '/tokens/summary') return sendJson(response, tokenSummary)
    if (adminPath === '/tokens/top-users') return sendJson(response, tokenTopUsers)
    if (adminPath === '/tokens/by-day') return sendJson(response, tokenByDay)
    if (adminPath === '/tokens/by-user') return sendJson(response, tokenByUser)

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
    connections.add(connection)
    const send = (frame: unknown) => connection.send(JSON.stringify(frame))

    send({ type: 'connection_established', session_id: 'a11y-session', message: 'connected', timestamp: TIMESTAMP })
    send({ type: 'auth_configured', auth_type: 'sso', display_name: 'Accessibility tester', message: 'Signed in', timestamp: TIMESTAMP })

    connection.on('close', () => connections.delete(connection))
    connection.on('message', (raw) => {
      const frame = JSON.parse(String(raw)) as ClientFrame
      sent.push(frame)

      if (frame.type === 'conversation_list') send(conversationList)
      if (frame.type === 'conversation_load') {
        send({
          ...(conversationLoaded as Record<string, unknown>),
          conversation_id: frame.conversation_id,
        })
      }
      if (frame.type === 'chat') {
        send({
          type: 'ai_response',
          timestamp: TIMESTAMP,
          conversation_id: frame.conversation_id ?? 'conversation-1',
          message_id: `a11y-${sent.length}`,
          message: 'Keyboard journey completed.',
          tool_calls: [],
          tool_results: [],
          metadata: {
            model_id: 'claude',
            model_name: 'Claude',
            tool_call_rounds: 0,
            total_tool_calls: 0,
            preprocessing_applied: false,
          },
        })
      }
    })
  })

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (address === null || typeof address === 'string') {
    throw new Error('accessibility server did not expose a TCP port')
  }

  return {
    origin: `http://127.0.0.1:${address.port}`,
    sentOf: (type) => sent.filter((frame) => frame.type === type),
    async close() {
      for (const connection of connections) connection.terminate()
      connections.clear()
      await new Promise<void>((resolve) => sockets.close(() => resolve()))
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error === undefined ? resolve() : reject(error)))
      })
    },
  }
}