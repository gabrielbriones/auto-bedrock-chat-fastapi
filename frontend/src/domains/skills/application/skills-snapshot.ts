import type { Problem } from '@/shared/http/exception'
import type { Logger } from '@/shared/logging/logger'
import type { ConfirmationPort } from '@/shared/ports/confirmation-port'
import type { NotificationPort } from '@/shared/ports/notification-port'

import type { SkillsGateway } from '@/domains/skills/application/ports'
import type { Skill, SkillLimits, SkillRevisionSummary, SkillSummary } from '@/domains/skills/domain/public'

export type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

export type SkillEditorMode =
  | { readonly mode: 'closed' }
  | { readonly mode: 'create' }
  | { readonly mode: 'edit'; readonly name: string }

export type SkillsSnapshot = {
  readonly skills: readonly SkillSummary[]
  /** The server's configured size limits; `null` until the first list load. */
  readonly limits: SkillLimits | null
  readonly listStatus: LoadStatus
  readonly listProblem: Problem | null
  /** Skills whose enable/disable request is in flight (their switch already shows the new state). */
  readonly toggling: readonly string[]
  /** The skill whose delete is in flight. */
  readonly deleting: string | null
  readonly editor: SkillEditorMode
  /** The stored skill the open editor started from. */
  readonly detail: Skill | null
  readonly detailStatus: LoadStatus
  readonly detailProblem: Problem | null
  readonly revisions: readonly SkillRevisionSummary[]
  readonly revisionsStatus: LoadStatus
  readonly saving: boolean
  /** Inline error for the last create/save (validation, duplicate name, access). */
  readonly saveProblem: Problem | null
  /** Set by a `409 revision_conflict`: the version another admin stored meanwhile. */
  readonly conflict: Skill | null
}

export type SkillsStoreOptions = {
  readonly gateway: SkillsGateway
  readonly confirmations: ConfirmationPort
  readonly notifications: NotificationPort
  readonly logger: Logger
}

export const CLOSED_EDITOR = {
  editor: { mode: 'closed' },
  detail: null,
  detailStatus: 'idle',
  detailProblem: null,
  revisions: [],
  revisionsStatus: 'idle',
  saving: false,
  saveProblem: null,
  conflict: null,
} as const satisfies Partial<SkillsSnapshot>

export const createInitialSkillsSnapshot = (): SkillsSnapshot => ({
  skills: [],
  limits: null,
  listStatus: 'idle',
  listProblem: null,
  toggling: [],
  deleting: null,
  ...CLOSED_EDITOR,
})
