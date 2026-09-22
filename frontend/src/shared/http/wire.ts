import { z } from 'zod'

import { Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'

// Wire timestamps are ISO-8601 strings produced by FastAPI's `jsonable_encoder`. A value that
// fails to parse is a contract failure, so it is reported as a schema issue rather than silently
// becoming the epoch.
export const instantSchema = z.string().transform((value, ctx) => {
  const parsed = Instant.fromIso(value)

  if (!isOk(parsed)) {
    ctx.addIssue({ code: 'custom', message: `not an ISO-8601 instant: ${value}` })
    return z.NEVER
  }

  return parsed.value
})

export const nullableInstantSchema = instantSchema.nullish().transform((value) => value ?? null)

export const nullableTextSchema = z
  .string()
  .nullish()
  .transform((value) => value ?? null)

export const issuesOf = (error: z.ZodError): readonly string[] =>
  error.issues.map((issue) => `${issue.path.join('.') || '(root)'}: ${issue.message}`)