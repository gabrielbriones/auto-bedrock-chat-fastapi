import type { Problem } from '@/shared/http/exception'
import type { ConfirmationPort } from '@/shared/ports/confirmation-port'
import type { Logger } from '@/shared/logging/logger'
import type { NotificationPort } from '@/shared/ports/notification-port'
import { DEFAULT_PAGE_LIMIT } from '@/shared/kernel/pagination'

import type { KnowledgeGateway, Page } from '@/domains/knowledge/application/ports'
import type { KbDocument, KbDocumentSummary } from '@/domains/knowledge/domain/public'

export type KnowledgeListStatus = 'idle' | 'loading' | 'ready' | 'error'

export type KnowledgeSnapshot = {
  readonly page: Page<KbDocumentSummary>
  readonly listStatus: KnowledgeListStatus
  readonly listProblem: Problem | null
  readonly activeDocument: KbDocument | null
  readonly detailStatus: KnowledgeListStatus
  readonly detailProblem: Problem | null
  readonly mutationPending: boolean
  readonly saveProblem: Problem | null
}

export type KnowledgeStoreOptions = {
  readonly gateway: KnowledgeGateway
  readonly confirmations: ConfirmationPort
  readonly notifications: NotificationPort
  readonly logger: Logger
}

export const emptyKnowledgePage = (
  limit = DEFAULT_PAGE_LIMIT,
  offset = 0,
): Page<KbDocumentSummary> => ({
  items: [],
  total: 0,
  limit,
  offset,
})

export const createInitialKnowledgeSnapshot = (): KnowledgeSnapshot => ({
  page: emptyKnowledgePage(),
  listStatus: 'idle',
  listProblem: null,
  activeDocument: null,
  detailStatus: 'idle',
  detailProblem: null,
  mutationPending: false,
  saveProblem: null,
})