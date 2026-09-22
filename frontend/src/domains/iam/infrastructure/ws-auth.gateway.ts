import type { Logger } from '@/shared/logging/logger'
import type { MessageBus, ServerFrame } from '@/shared/ws/message-bus'
import type { SendResult, SocketClient } from '@/shared/ws/socket-client'

import type { AuthEvent, AuthGateway } from '@/domains/iam/application/ports'
import {
  toCredentialKind,
  type Credential,
} from '@/domains/iam/domain/credential'

type SocketWriter = Pick<SocketClient, 'send'>
type AuthFrameSource = Pick<MessageBus, 'subscribe'>

const toAuthFrame = (credential: Credential): object => {
  switch (credential.kind) {
    case 'bearer_token':
      return { type: 'auth', auth_type: credential.kind, token: credential.token }
    case 'basic_auth':
      return {
        type: 'auth',
        auth_type: credential.kind,
        username: credential.username,
        password: credential.password,
      }
    case 'api_key':
      return {
        type: 'auth',
        auth_type: credential.kind,
        api_key: credential.apiKey,
        api_key_header: credential.header,
      }
    case 'oauth2_client_credentials':
      return {
        type: 'auth',
        auth_type: credential.kind,
        client_id: credential.clientId,
        client_secret: credential.clientSecret,
        token_url: credential.tokenUrl,
        ...(credential.scope === undefined ? {} : { scope: credential.scope }),
      }
    case 'custom':
      return {
        type: 'auth',
        auth_type: credential.kind,
        custom_headers: credential.headers,
      }
    case 'sso':
      return { type: 'auth', auth_type: credential.kind }
  }
}

// NFR-SEC-003: `redirect_url` is server-supplied, so an off-origin value is an open redirect
// waiting for its first caller. It is dropped here, at the boundary, rather than trusted by the
// domain — including values that are not URLs at all (`javascript:`, `//evil.test`).
const toSafeRedirect = (
  value: string | undefined,
  origin: string,
  onRejected: (url: string) => void,
): { redirectUrl?: string } => {
  if (value === undefined) {
    return {}
  }

  try {
    if (new URL(value, origin).origin === origin) {
      return { redirectUrl: value }
    }
  } catch {
    // An unparseable value is treated exactly like an off-origin one.
  }

  onRejected(value)
  return {}
}

const toAuthEvent = (
  frame: ServerFrame,
  origin: string,
  onRejectedRedirect: (url: string) => void,
): AuthEvent | null => {
  switch (frame.type) {
    case 'auth_configured': {
      const authKind = toCredentialKind(frame.auth_type)
      return authKind === null
        ? null
        : {
            kind: 'configured',
            authKind,
            message: frame.message,
            ...(frame.display_name === undefined ? {} : { displayName: frame.display_name }),
          }
    }
    case 'auth_failed': {
      const authKind = toCredentialKind(frame.auth_type)
      return authKind === null
        ? null
        : {
            kind: 'failed',
            authKind,
            message: frame.message,
            ...toSafeRedirect(frame.redirect_url, origin, onRejectedRedirect),
          }
    }
    case 'auth_expired':
      return {
        kind: 'expired',
        message: frame.message,
        ...toSafeRedirect(frame.redirect_url, origin, onRejectedRedirect),
      }
    case 'logout_success':
      return { kind: 'logged-out', message: frame.message }
    default:
      return null
  }
}

export class WsAuthGateway implements AuthGateway {
  readonly #frames: AuthFrameSource
  readonly #socket: SocketWriter
  readonly #logger: Logger
  readonly #origin: string
  #reportedRejectedRedirect = false

  constructor(socket: SocketWriter, frames: AuthFrameSource, logger: Logger, origin: string) {
    this.#socket = socket
    this.#frames = frames
    this.#logger = logger
    this.#origin = origin
  }

  authenticate(credential: Credential): SendResult {
    return this.#socket.send(JSON.stringify(toAuthFrame(credential)))
  }

  logout(): SendResult {
    return this.#socket.send(JSON.stringify({ type: 'logout' }))
  }

  onAuthEvent(listener: (event: AuthEvent) => void): () => void {
    return this.#frames.subscribe((frame) => {
      const event = toAuthEvent(frame, this.#origin, (url) => {
        // Once per socket session: a misconfigured backend would otherwise log on every frame.
        if (!this.#reportedRejectedRedirect) {
          this.#reportedRejectedRedirect = true
          this.#logger.warn('auth_redirect_rejected', { url, origin: this.#origin })
        }
      })

      if (event !== null) {
        listener(event)
      }
    })
  }
}