import { z } from 'zod'

import { invalidResponseProblem, type Problem } from '@/shared/http/exception'
import { instantSchema, issuesOf } from '@/shared/http/wire'
import { CalendarDate } from '@/shared/kernel/instant'
import { err, isOk, ok, type Result } from '@/shared/kernel/result'

import type {
  DailyUsageRow,
  ModelUsageRow,
  SessionUsageRow,
  UserUsageRow,
} from '@/domains/telemetry/domain/public'
import { createTokenCount } from '@/domains/telemetry/domain/public'

const tokenCountFields = {
  input_tokens: z.number().int().min(0),
  output_tokens: z.number().int().min(0),
} as const

const modelUsageItemSchema = z.object({
  model_id: z.string(),
  ...tokenCountFields,
  turn_count: z.number().int().min(0),
})

const topUserItemSchema = z.object({
  user_id: z.string(),
  ...tokenCountFields,
})

const dailyUsageItemSchema = z.object({
  date: z.string().transform((value, context) => {
    const parsed = CalendarDate.fromIso(value)

    if (!isOk(parsed)) {
      context.addIssue({ code: 'custom', message: `not a calendar date: ${value}` })
      return z.NEVER
    }

    return parsed.value
  }),
  ...tokenCountFields,
  turn_count: z.number().int().min(0),
})

const sessionUsageItemSchema = z.object({
  session_id: z.string(),
  model_id: z.string(),
  ...tokenCountFields,
  turn_ts: instantSchema,
})

const summaryResponseSchema = z.object({ items: z.array(modelUsageItemSchema) })
const topUsersResponseSchema = z.object({ items: z.array(topUserItemSchema) })
const byDayResponseSchema = z.object({ items: z.array(dailyUsageItemSchema) })
const byUserResponseSchema = z.object({ user_id: z.string(), items: z.array(sessionUsageItemSchema) })

type ModelUsageItem = z.output<typeof modelUsageItemSchema>
type TopUserItem = z.output<typeof topUserItemSchema>
type DailyUsageItem = z.output<typeof dailyUsageItemSchema>
type SessionUsageItem = z.output<typeof sessionUsageItemSchema>

const toModelUsageRow = (item: ModelUsageItem): ModelUsageRow => ({
  modelId: item.model_id,
  tokens: createTokenCount(item.input_tokens, item.output_tokens),
  turnCount: item.turn_count,
})

const toUserUsageRow = (item: TopUserItem): UserUsageRow => ({
  userId: item.user_id,
  tokens: createTokenCount(item.input_tokens, item.output_tokens),
})

const toDailyUsageRow = (item: DailyUsageItem): DailyUsageRow => ({
  date: item.date,
  tokens: createTokenCount(item.input_tokens, item.output_tokens),
  turnCount: item.turn_count,
})

const toSessionUsageRow = (item: SessionUsageItem): SessionUsageRow => ({
  sessionId: item.session_id,
  modelId: item.model_id,
  tokens: createTokenCount(item.input_tokens, item.output_tokens),
  turnAt: item.turn_ts,
})

const invalid = (title: string, error: z.ZodError): Result<never, Problem> =>
  err(invalidResponseProblem(title, issuesOf(error)))

export const toModelUsageRows = (value: unknown): Result<readonly ModelUsageRow[], Problem> => {
  const parsed = summaryResponseSchema.safeParse(value)

  return parsed.success
    ? ok(parsed.data.items.map(toModelUsageRow))
    : invalid('Invalid token usage summary', parsed.error)
}

export const toUserUsageRows = (value: unknown): Result<readonly UserUsageRow[], Problem> => {
  const parsed = topUsersResponseSchema.safeParse(value)

  return parsed.success
    ? ok(parsed.data.items.map(toUserUsageRow))
    : invalid('Invalid top-user token usage', parsed.error)
}

export const toDailyUsageRows = (value: unknown): Result<readonly DailyUsageRow[], Problem> => {
  const parsed = byDayResponseSchema.safeParse(value)

  return parsed.success
    ? ok(parsed.data.items.map(toDailyUsageRow))
    : invalid('Invalid daily token usage', parsed.error)
}

export const toSessionUsageRows = (value: unknown): Result<readonly SessionUsageRow[], Problem> => {
  const parsed = byUserResponseSchema.safeParse(value)

  return parsed.success
    ? ok(parsed.data.items.map(toSessionUsageRow))
    : invalid('Invalid per-user token usage', parsed.error)
}

export const toUtcBoundary = (date: CalendarDate): string => `${date.toIso()}T00:00:00.000Z`