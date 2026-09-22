import { HttpResponse, http } from 'msw'

import bootstrapConfig from '../fixtures/bootstrap-config.json'

// BC-001 (CONTRACT-001 §6): GET {CHAT}/config. Base path matches the
// VITE_CHAT_BASE default (`/bedrock-chat`) documented in STD-001 §9.
export const bootstrapHandlers = [
  http.get('*/bedrock-chat/config', () => HttpResponse.json(bootstrapConfig)),
]

// Failure-path overrides for T-021 (`loadBootstrap`/`BootstrapProvider` tests). Install one via
// `server.use(bootstrapMalformedHandler)` etc. to replace the default success handler above for
// a single test.
export const bootstrapMalformedHandler = http.get('*/bedrock-chat/config', () =>
  HttpResponse.json({ ...bootstrapConfig, websocketUrl: undefined }),
)

export const bootstrapHtml502Handler = http.get(
  '*/bedrock-chat/config',
  () =>
    new HttpResponse('<html><body>Bad Gateway</body></html>', {
      status: 502,
      statusText: 'Bad Gateway',
      headers: { 'content-type': 'text/html' },
    }),
)

export const bootstrapNetworkErrorHandler = http.get('*/bedrock-chat/config', () =>
  HttpResponse.error(),
)
