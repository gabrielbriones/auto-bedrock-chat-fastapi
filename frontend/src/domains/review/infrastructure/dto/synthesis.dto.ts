import { z } from 'zod'

import { feedbackEntryId, kbDocumentId } from '@/shared/kernel/branded'
import { invalidResponseProblem, type Problem } from '@/shared/http/exception'
import { err, ok, type Result } from '@/shared/kernel/result'

import type { RollbackOutcome, SynthesisOutcome } from '@/domains/review/application/ports'
import type { SynthesisPhase } from '@/domains/review/domain/public'
import { instantSchema, issuesOf, nullableTextSchema } from '@/domains/review/infrastructure/dto/wire'

// BC-007 is already served: the live `GET {ADMIN}/synthesis/status` returns the whole run record,
// not the bare `{phase}` CONTRACT-001 describes. Only `phase` is consumed — FR-REV-016 needs to
// know whether a batch run is in flight, nothing more.
export const synthesisStatusDtoSchema = z.object({
  phase: z.enum(['idle', 'running', 'completed', 'failed']),
})

export const singleEntrySynthesisDtoSchema = z.object({
  tag: z.string(),
  action: z.string(),
  kb_doc_id: nullableTextSchema,
  feedback_ids_marked: z.array(z.string()).default([]),
})

export const rollbackDtoSchema = z.object({
  article_id: z.string().min(1),
  rolled_back_at: instantSchema,
  rolled_back_by: z.string(),
  reason: nullableTextSchema,
  feedback_entries_reverted: z.number().int().min(0),
})

export const toSynthesisPhase = (value: unknown): Result<SynthesisPhase, Problem> => {
  const parsed = synthesisStatusDtoSchema.safeParse(value)

  return parsed.success
    ? ok(parsed.data.phase)
    : err(invalidResponseProblem('Invalid synthesis status', issuesOf(parsed.error)))
}

export const toSynthesisOutcome = (value: unknown): Result<SynthesisOutcome, Problem> => {
  const parsed = singleEntrySynthesisDtoSchema.safeParse(value)

  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid synthesis result', issuesOf(parsed.error)))
  }

  const dto = parsed.data

  return ok({
    tag: dto.tag,
    action: dto.action,
    kbDocumentId: dto.kb_doc_id === null ? null : kbDocumentId(dto.kb_doc_id),
    markedEntryIds: dto.feedback_ids_marked.map(feedbackEntryId),
  })
}

export const toRollbackOutcome = (value: unknown): Result<RollbackOutcome, Problem> => {
  const parsed = rollbackDtoSchema.safeParse(value)

  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid rollback result', issuesOf(parsed.error)))
  }

  const dto = parsed.data

  return ok({
    kbDocumentId: kbDocumentId(dto.article_id),
    rolledBackAt: dto.rolled_back_at,
    rolledBackBy: dto.rolled_back_by,
    reason: dto.reason,
    entriesReverted: dto.feedback_entries_reverted,
  })
}
