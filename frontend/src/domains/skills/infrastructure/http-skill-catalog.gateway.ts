import type { HttpClient } from '@/shared/http/http-client'
import type { Problem } from '@/shared/http/exception'
import { andThen, type Result } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

import type { SkillCatalogGateway } from '@/domains/skills/application/ports'
import type { SkillCatalogEntry } from '@/domains/skills/domain/public'
import { toSkillCatalog } from '@/domains/skills/infrastructure/dto/skill-catalog.dto'

type CatalogHttpClient = Pick<HttpClient, 'request'>

// skills.md "User catalog": `GET {chat_endpoint}/skills/catalog`, for any user allowed to chat.
export class HttpSkillCatalogGateway implements SkillCatalogGateway {
  private readonly chatPrefix: string
  private readonly httpClient: CatalogHttpClient
  private readonly logger: Logger

  constructor(chatPrefix: string, httpClient: CatalogHttpClient, logger: Logger) {
    this.chatPrefix = chatPrefix
    this.httpClient = httpClient
    this.logger = logger
  }

  async list(signal: AbortSignal): Promise<Result<readonly SkillCatalogEntry[], Problem>> {
    const response = await this.httpClient.request<unknown>(`${this.chatPrefix}/skills/catalog`, {
      method: 'GET',
      signal,
      credentials: 'include',
      logger: this.logger,
    })
    return andThen(response, toSkillCatalog)
  }
}
