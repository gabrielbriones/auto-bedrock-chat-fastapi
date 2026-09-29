import { z } from 'zod'

import type { LogContext, Logger } from '@/shared/logging/logger'

export type ContextId = 'iam' | 'messaging' | 'feedback' | 'model-config' | 'conversation'

const serverFrame = <Type extends string>(type: Type) =>
  z
    .object({
      type: z.literal(type),
      timestamp: z.string().min(1),
    })
    .strict()

const metadataSchema = z
  .object({
    // The backend copies the graph's own per-message metadata wholesale before adding the
    // fields below, so these three always ride along too (CONTRACT-001 undocumented, verified
    // against a live backend response). Kept optional: a rejected frame is dropped silently by
    // `receive`, which freezes the UI on "AI is typing", so metadata is never worth failing on.
    message_id: z.string().optional(),
    usage: z
      .object({
        // Bedrock's usage_metadata is read with `.get()`, so either count can arrive as null.
        input_tokens: z.number().nullable().optional(),
        output_tokens: z.number().nullable().optional(),
      })
      .strict()
      .optional(),
    timestamp: z.string().min(1).optional(),
    model_id: z.string(),
    model_name: z.string(),
    tool_call_rounds: z.number(),
    total_tool_calls: z.number(),
    preprocessing_applied: z.boolean(),
    rejected_overrides: z.record(z.string(), z.unknown()).optional(),
    input_tokens: z.number().optional(),
    output_tokens: z.number().optional(),
    // Null whenever Bedrock's response_metadata carries no stopReason.
    stop_reason: z.string().nullable().optional(),
    kb_used: z.boolean().optional(),
    kb_chunks: z.number().optional(),
    kb_sources: z
      .array(
        z
          .object({
            document_id: z.string().nullable(),
            title: z.string().nullable(),
            source: z.string().nullable(),
            // `source_url` is a nullable column, so a document without a link sends null here.
            url: z.string().nullable(),
            score: z.number(),
          })
          .strict(),
      )
      .optional(),
  })
  .strict()

const messageSchema = z
  .object({
    message_id: z.string().nullable().optional(),
    role: z.string().nullable().optional(),
    content: z.string(),
    timestamp: z.string().min(1).nullable().optional(),
    tool_calls: z.array(z.unknown()),
    tool_results: z.array(z.unknown()),
    metadata: z.record(z.string(), z.unknown()),
  })
  .strict()

const conversationSummarySchema = z
  .object({
    id: z.string(),
    title: z.string(),
    updated_at: z.string().min(1),
    message_count: z.number(),
  })
  .strict()

const aiResponseFrameSchema = serverFrame('ai_response')
  .extend({
    message: z.string(),
    error: z.literal(true).optional(),
    message_id: z.string().optional(),
    tool_calls: z.array(z.unknown()).optional(),
    tool_results: z.array(z.unknown()).optional(),
    metadata: metadataSchema.optional(),
    conversation_id: z.string().optional(),
  })
  .superRefine((frame, context) => {
    if (frame.error === true) {
      return
    }

    for (const field of ['message_id', 'tool_calls', 'tool_results', 'metadata', 'conversation_id'] as const) {
      if (frame[field] === undefined) {
        context.addIssue({
          code: 'custom',
          message: `${field} is required unless error is true`,
          path: [field],
        })
      }
    }
  })

export const ServerFrameSchema = z.discriminatedUnion('type', [
  serverFrame('connection_established').extend({
    session_id: z.string(),
    message: z.string(),
  }),
  serverFrame('auth_configured').extend({
    message: z.string(),
    auth_type: z.string(),
    display_name: z.string().optional(),
  }),
  serverFrame('auth_failed').extend({
    message: z.string(),
    auth_type: z.string(),
    redirect_url: z.string().optional(),
  }),
  serverFrame('auth_expired').extend({
    message: z.string(),
    redirect_url: z.string().optional(),
  }),
  serverFrame('logout_success').extend({ message: z.string() }),
  serverFrame('typing').extend({ message: z.string() }),
  aiResponseFrameSchema,
  serverFrame('error').extend({ message: z.string() }),
  serverFrame('pong'),
  serverFrame('history').extend({ messages: z.array(messageSchema) }),
  serverFrame('history_cleared').extend({ message: z.string() }),
  serverFrame('feedback_ack').extend({
    message_id: z.string(),
    feedback_id: z.string(),
    status: z.string(),
  }),
  serverFrame('feedback_error').extend({
    code: z.string(),
    message: z.string(),
    message_id: z.string().optional(),
  }),
  serverFrame('config_updated').extend({
    active_overrides: z.record(z.string(), z.unknown()),
    applied_overrides: z.record(z.string(), z.unknown()),
    rejected_overrides: z.array(z.unknown()),
  }),
  serverFrame('conversation_created').extend({ conversation_id: z.string() }),
  serverFrame('conversation_titled').extend({
    conversation_id: z.string(),
    title: z.string(),
  }),
  serverFrame('conversation_list').extend({ conversations: z.array(conversationSummarySchema) }),
  serverFrame('conversation_loaded').extend({
    conversation_id: z.string(),
    conversation: z.record(z.string(), z.unknown()),
    messages: z.array(messageSchema),
  }),
  serverFrame('conversation_renamed').extend({
    conversation_id: z.string(),
    title: z.string(),
  }),
  serverFrame('conversation_deleted').extend({ conversation_id: z.string() }),
  serverFrame('conversation_bulk_deleted').extend({
    deleted_ids: z.array(z.string()),
    active_conversation_deleted: z.boolean(),
  }),
  serverFrame('conversation_all_deleted').extend({ deleted_count: z.number() }),
  serverFrame('conversation_error').extend({
    code: z.string(),
    message: z.string(),
    conversation_id: z.string().optional(),
  }),
])

export type ServerFrame = z.infer<typeof ServerFrameSchema>
export type ServerFrameType = ServerFrame['type']

// CONTRACT-001 §2.4: `satisfies` makes a new server frame a compile error until it has an owner.
export const FRAME_OWNER = {
  connection_established: 'messaging',
  auth_configured: 'iam',
  auth_failed: 'iam',
  auth_expired: 'iam',
  logout_success: 'iam',
  typing: 'messaging',
  ai_response: 'messaging',
  error: 'messaging',
  pong: 'messaging',
  history: 'messaging',
  history_cleared: 'messaging',
  feedback_ack: 'feedback',
  feedback_error: 'feedback',
  config_updated: 'model-config',
  conversation_created: 'conversation',
  conversation_titled: 'conversation',
  conversation_list: 'conversation',
  conversation_loaded: 'conversation',
  conversation_renamed: 'conversation',
  conversation_deleted: 'conversation',
  conversation_bulk_deleted: 'conversation',
  conversation_all_deleted: 'conversation',
  conversation_error: 'conversation',
} satisfies Record<ServerFrameType, ContextId>

export type ServerFrameSubscriber = (frame: ServerFrame) => void

export type MalformedFrameReason = 'non-string' | 'invalid-json'

const UNTYPED_FRAME_KEY = '<untyped>'
const MAX_SAMPLE_LENGTH = 200

const describeFrame = (rawFrame: unknown): string =>
  rawFrame === null || rawFrame === undefined
    ? String(rawFrame)
    : (rawFrame.constructor?.name ?? typeof rawFrame)

// A frame that failed JSON.parse cannot be redacted structurally; truncation is the best
// available bound until `redactSensitive` (T-074) exists.
const sampleOf = (rawFrame: string): string =>
  rawFrame.length > MAX_SAMPLE_LENGTH ? `${rawFrame.slice(0, MAX_SAMPLE_LENGTH)}…` : rawFrame

export class MessageBus {
  private readonly logger: Logger
  private readonly subscribers = new Set<ServerFrameSubscriber>()
  private readonly malformedFrameCounts = new Map<string, number>()
  private readonly unknownFrameCounts = new Map<string, number>()
  private readonly invalidFrameCounts = new Map<string, number>()

  constructor(logger: Logger) {
    this.logger = logger
  }

  subscribe(subscriber: ServerFrameSubscriber): () => void {
    this.subscribers.add(subscriber)
    return () => this.subscribers.delete(subscriber)
  }

  malformedFrameCount(reason: MalformedFrameReason): number {
    return this.malformedFrameCounts.get(reason) ?? 0
  }

  unknownFrameCount(type: string): number {
    return this.unknownFrameCounts.get(type) ?? 0
  }

  invalidFrameCount(type: string): number {
    return this.invalidFrameCounts.get(type) ?? 0
  }

  receive(rawFrame: unknown): void {
    let candidate: unknown

    if (typeof rawFrame !== 'string') {
      this.countAndLogOnce(this.malformedFrameCounts, 'non-string', 'ws_frame_malformed', () => ({
        reason: 'non-string',
        frame: describeFrame(rawFrame),
      }))
      return
    }

    try {
      candidate = JSON.parse(rawFrame)
    } catch {
      this.countAndLogOnce(this.malformedFrameCounts, 'invalid-json', 'ws_frame_malformed', () => ({
        reason: 'invalid-json',
        sample: sampleOf(rawFrame),
      }))
      return
    }

    const type = this.frameType(candidate)
    if (type !== undefined && !Object.hasOwn(FRAME_OWNER, type)) {
      this.countAndLogOnce(this.unknownFrameCounts, type, 'ws_frame_unknown', () => ({ type }))
      return
    }

    const parsedFrame = ServerFrameSchema.safeParse(candidate)
    if (!parsedFrame.success) {
      this.countAndLogOnce(
        this.invalidFrameCounts,
        type ?? UNTYPED_FRAME_KEY,
        'ws_frame_invalid',
        () => ({
          type,
          // Paths and codes only: issue values would carry payload content into the log.
          issues: parsedFrame.error.issues.map((issue) => ({
            path: issue.path.join('.'),
            code: issue.code,
          })),
        }),
      )
      return
    }

    for (const subscriber of this.subscribers) {
      subscriber(parsedFrame.data)
    }
  }

  // NFR-OBS-003: every contract violation is reported once per frame type per session.
  private countAndLogOnce(
    counts: Map<string, number>,
    key: string,
    event: string,
    context: () => LogContext,
  ): void {
    const count = (counts.get(key) ?? 0) + 1
    counts.set(key, count)

    if (count === 1) {
      this.logger.warn(event, context())
    }
  }

  private frameType(candidate: unknown): string | undefined {
    if (typeof candidate !== 'object' || candidate === null || !('type' in candidate)) {
      return undefined
    }

    const { type } = candidate
    return typeof type === 'string' ? type : undefined
  }
}