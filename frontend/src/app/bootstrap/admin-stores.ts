import type { HttpClient } from '@/shared/http/http-client'
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
import { TelemetryStore } from '@/domains/telemetry/application/telemetry.store'
import { HttpTelemetryGateway } from '@/domains/telemetry/infrastructure/http-telemetry.gateway'

export type AdminStoreParts = {
  readonly httpClient: HttpClient
  readonly logger: Logger
  readonly confirmations: ConfirmationPort
  readonly notifications: NotificationPort
}

// The admin-only, HTTP-backed stores (SPEC-016 §3): request/response reads against the admin
// prefix rather than live socket sessions, so they are built together off the same client.
export const createAdminStores = (bootstrap: ChatBootstrap, parts: AdminStoreParts) => {
  const { httpClient, logger, confirmations, notifications } = parts
  const reviewGateway = new HttpReviewGateway(bootstrap.adminPrefix, httpClient, logger)
  const knowledgeGateway = new HttpKnowledgeGateway(bootstrap.adminPrefix, httpClient, logger)
  const kbSourcesGateway = new HttpKbSourcesGateway(bootstrap.adminPrefix, httpClient, logger)
  const telemetryGateway = new HttpTelemetryGateway(bootstrap.adminPrefix, httpClient, logger)

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
  }
}
