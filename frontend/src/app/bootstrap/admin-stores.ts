import type { HttpClient } from '@/shared/http/http-client'
import type { Clock } from '@/shared/kernel/instant'
import type { Logger } from '@/shared/logging/logger'
import type { ConfirmationPort } from '@/shared/ports/confirmation-port'
import type { NotificationPort } from '@/shared/ports/notification-port'

import type { ChatBootstrap } from '@/app/bootstrap/chat-bootstrap'
import { KbSourcesStore } from '@/domains/knowledge/application/kb-sources.store'
import { KnowledgeStore } from '@/domains/knowledge/application/knowledge.store'
import { HttpKbSourcesGateway } from '@/domains/knowledge/infrastructure/http-kb-sources.gateway'
import { HttpKnowledgeGateway } from '@/domains/knowledge/infrastructure/http-knowledge.gateway'
import { TimeoutPollScheduler } from '@/domains/knowledge/infrastructure/timeout-poll.scheduler'
import { ReviewStore } from '@/domains/review/application/review.store'
import { HttpReviewGateway } from '@/domains/review/infrastructure/http-review.gateway'
import { SkillsStore } from '@/domains/skills/application/skills.store'
import { SkillCatalogStore } from '@/domains/skills/application/skill-catalog.store'
import type { SkillCatalogGateway } from '@/domains/skills/application/ports'
import { HttpSkillsGateway } from '@/domains/skills/infrastructure/http-skills.gateway'
import { TelemetryStore } from '@/domains/telemetry/application/telemetry.store'
import { HttpTelemetryGateway } from '@/domains/telemetry/infrastructure/http-telemetry.gateway'

export type AdminStoreParts = {
  readonly httpClient: HttpClient
  readonly logger: Logger
  readonly clock: Clock
  readonly confirmations: ConfirmationPort
  readonly notifications: NotificationPort
  /** Socket-backed: the slash-command menu's data, read with the chat's own credentials. */
  readonly skillCatalogGateway: SkillCatalogGateway
}

// The HTTP-backed admin stores (SPEC-016 §3): request/response reads rather than live socket
// sessions, so they are built together off the same client. The user-facing skill catalog rides
// along only to keep the container small.
export const createAdminStores = (bootstrap: ChatBootstrap, parts: AdminStoreParts) => {
  const { httpClient, logger, clock, confirmations, notifications, skillCatalogGateway } = parts
  const reviewGateway = new HttpReviewGateway(bootstrap.adminPrefix, httpClient, logger)
  const knowledgeGateway = new HttpKnowledgeGateway(bootstrap.adminPrefix, httpClient, logger)
  const kbSourcesGateway = new HttpKbSourcesGateway(bootstrap.adminPrefix, httpClient, logger)
  const telemetryGateway = new HttpTelemetryGateway(bootstrap.adminPrefix, httpClient, logger)
  const skillsGateway = new HttpSkillsGateway(bootstrap.adminPrefix, httpClient, logger)

  return {
    reviewGateway,
    reviews: new ReviewStore({ gateway: reviewGateway, confirmations, notifications, logger }),
    knowledgeGateway,
    knowledge: new KnowledgeStore({ gateway: knowledgeGateway, confirmations, notifications, logger }),
    kbSourcesGateway,
    kbSources: new KbSourcesStore({
      gateway: kbSourcesGateway,
      confirmations,
      notifications,
      scheduler: new TimeoutPollScheduler(),
      logger,
    }),
    telemetryGateway,
    telemetry: new TelemetryStore({ gateway: telemetryGateway }),
    skills: new SkillsStore({ gateway: skillsGateway, confirmations, notifications, logger }),
    skillCatalog: new SkillCatalogStore({ gateway: skillCatalogGateway, clock, logger }),
  }
}
