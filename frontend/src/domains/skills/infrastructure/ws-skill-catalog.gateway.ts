import { abortedProblem, networkErrorProblem, type Problem } from '@/shared/http/exception'
import { err, type Result } from '@/shared/kernel/result'
import type { MessageBus } from '@/shared/ws/message-bus'
import type { SocketClient } from '@/shared/ws/socket-client'

import type { SkillCatalogGateway } from '@/domains/skills/application/ports'
import type { SkillCatalogEntry } from '@/domains/skills/domain/public'
import { toSkillCatalog } from '@/domains/skills/infrastructure/dto/skill-catalog.dto'

type SocketWriter = Pick<SocketClient, 'send'>
type FrameSource = Pick<MessageBus, 'subscribe'>
type CatalogResult = Result<readonly SkillCatalogEntry[], Problem>

// skills.md "User catalog", asked over the socket: it carries whatever credential the user signed in
// with, where the HTTP route only sees the SSO cookie, and it needs no chat-prefix guess.
export class WsSkillCatalogGateway implements SkillCatalogGateway {
  readonly #frames: FrameSource
  readonly #socket: SocketWriter

  constructor(socket: SocketWriter, frames: FrameSource) {
    this.#socket = socket
    this.#frames = frames
  }

  list(signal: AbortSignal): Promise<CatalogResult> {
    return new Promise((resolve) => {
      const finish = (result: CatalogResult) => {
        unsubscribe()
        signal.removeEventListener('abort', abort)
        resolve(result)
      }
      const abort = () => finish(err(abortedProblem()))
      const unsubscribe = this.#frames.subscribe((frame) => {
        if (frame.type !== 'skill_catalog') {
          return
        }
        finish(
          frame.error === undefined
            ? toSkillCatalog({ items: frame.items })
            : err({ code: 'http-error', title: frame.error.message, serverCode: frame.error.code }),
        )
      })

      if (signal.aborted) {
        abort()
        return
      }
      signal.addEventListener('abort', abort)
      if (this.#socket.send(JSON.stringify({ type: 'skill_catalog' })) === 'dropped-closed') {
        finish(err(networkErrorProblem('socket closed')))
      }
    })
  }
}
