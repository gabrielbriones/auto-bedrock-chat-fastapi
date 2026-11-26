import type { Problem } from '@/shared/http/exception'
import type { Result } from '@/shared/kernel/result'

import type {
  Skill,
  SkillContentPatch,
  SkillCreateRequest,
  SkillLimits,
  SkillRevisionSummary,
  SkillSummary,
} from '@/domains/skills/domain/public'

export type SkillList = {
  readonly items: readonly SkillSummary[]
  readonly limits: SkillLimits
}

// A content write either lands, loses an optimistic-concurrency race (`409 revision_conflict`,
// carrying the stored skill so the editor can show it), or fails for any other reason.
export type SkillWriteError =
  | { readonly kind: 'conflict'; readonly current: Skill }
  | { readonly kind: 'problem'; readonly problem: Problem }

// admin-api.md "Skill management" (`/admin/skills*`).
export interface SkillsGateway {
  list(signal: AbortSignal): Promise<Result<SkillList, Problem>>
  get(name: string, signal: AbortSignal): Promise<Result<Skill, Problem>>
  create(request: SkillCreateRequest): Promise<Result<Skill, Problem>>
  patch(name: string, expectedRevision: number, changes: SkillContentPatch): Promise<Result<Skill, SkillWriteError>>
  rollback(name: string, revision: number, expectedRevision: number): Promise<Result<Skill, SkillWriteError>>
  setEnabled(name: string, enabled: boolean): Promise<Result<Skill, Problem>>
  remove(name: string): Promise<Result<void, Problem>>
  listRevisions(name: string, signal: AbortSignal): Promise<Result<readonly SkillRevisionSummary[], Problem>>
}
