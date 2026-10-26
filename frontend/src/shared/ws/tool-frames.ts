import { z } from 'zod'

// One frame as each tool call starts and one as it finishes, so a turn can show its calls live.
// Scoped to the turn's conversation like `typing`, so a client that switched away can ignore them.
const toolFrameFields = {
  timestamp: z.string().min(1),
  tool_call_id: z.string().nullable(),
  name: z.string().nullable(),
  conversation_id: z.string().nullable().optional(),
}

export const toolCallStartFrameSchema = z
  .object({
    type: z.literal('tool_call_start'),
    ...toolFrameFields,
    arguments: z.unknown().optional(),
    position: z.number(),
    total: z.number(),
  })
  .strict()

export const toolCallEndFrameSchema = z
  .object({
    type: z.literal('tool_call_end'),
    ...toolFrameFields,
    status: z.enum(['success', 'error']),
    error: z.string().nullable().optional(),
    duration_ms: z.number().optional(),
  })
  .strict()
