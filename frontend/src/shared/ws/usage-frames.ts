import { z } from 'zod'

// Mirrors the backend's `UsagePurpose`; the order is the one the UI lists them in.
export const USAGE_PURPOSES = ['chat', 'summarization', 'retry', 'title', 'embedding'] as const
export type UsagePurpose = (typeof USAGE_PURPOSES)[number]

// `.strict()`: every key the backend can send must be listed, or a frame carrying it is dropped.
export const usageSchema = z
  .object({
    // Bedrock's usage_metadata is read with `.get()`, so either count can arrive as null.
    input_tokens: z.number().nullable().optional(),
    output_tokens: z.number().nullable().optional(),
    // Prompt-caching subsets of input_tokens (XMGPLAT-11867).
    cache_read_tokens: z.number().nullable().optional(),
    cache_write_tokens: z.number().nullable().optional(),
    // Per-turn summary across every AI call of the turn (XMGPLAT-11865).
    cache_read: z.number().optional(),
    cache_write: z.number().optional(),
    // Only the purposes that made a call.
    by_purpose: z
      .partialRecord(z.enum(USAGE_PURPOSES), z.object({ input_tokens: z.number(), output_tokens: z.number() }).strict())
      .optional(),
    // Latest chat call's prompt + answer against the model's input window; absent when the window is unknown.
    context: z.object({ used_tokens: z.number(), max_tokens: z.number() }).strict().optional(),
    estimated_cost: z.number().nullable().optional(),
    currency: z.string().nullable().optional(),
    final: z.boolean().optional(),
  })
  .strict()

export type WireUsage = z.infer<typeof usageSchema>

// The final totals of a turn whose `ai_response` carried `usage.final: false`.
export const usageUpdatedFrameSchema = z
  .object({
    type: z.literal('usage_updated'),
    timestamp: z.string().min(1),
    message_id: z.string(),
    usage: usageSchema,
  })
  .strict()
