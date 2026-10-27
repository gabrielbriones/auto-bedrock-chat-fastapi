import type {
  LiveToolCall,
  LiveToolStatus,
  ToolCall,
  ToolResult,
  TurnActivity,
} from '@/domains/messaging/domain/turn'
import { redactSensitive } from '@/domains/messaging/presentation/redaction'

const SUMMARY_MAX_LENGTH = 80

export type ToolCallRow = {
  readonly id: string
  readonly name: string
  readonly arguments: unknown
  readonly status: LiveToolStatus
  /** Undefined while the call runs, or when the server has not sent its output (yet). */
  readonly output?: unknown
  readonly error: unknown
  readonly durationMs: number | null
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

export const formatValue = (value: unknown): string => {
  const visited = new WeakSet<object>()
  const redacted = redactSensitive(value)
  const serialized = JSON.stringify(
    redacted,
    (_key, nested: unknown) => {
      if (typeof nested !== 'object' || nested === null) {
        return nested
      }
      if (visited.has(nested)) {
        return '[Circular]'
      }
      visited.add(nested)
      return nested
    },
    2,
  )
  return serialized ?? String(redacted)
}

// The scalar arguments, inline, so a row says *what* it looked up without being expanded.
export const summarizeArguments = (args: unknown): string => {
  const redacted = redactSensitive(args)
  if (!isRecord(redacted)) {
    return ''
  }

  const text = Object.entries(redacted)
    .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(', ')
  return text.length > SUMMARY_MAX_LENGTH ? `${text.slice(0, SUMMARY_MAX_LENGTH - 1)}…` : text
}

export const formatDuration = (durationMs: number): string =>
  durationMs < 1000 ? `${durationMs} ms` : `${(durationMs / 1000).toFixed(1)} s`

const resultFor = (call: ToolCall, results: readonly ToolResult[]): ToolResult | undefined =>
  results.find((result) => result.toolCallId === call.id) ?? results.find((result) => result.name === call.name)

export const rowsFromActivity = (activity: TurnActivity): readonly ToolCallRow[] =>
  activity.toolCalls.map((call) => {
    const result = resultFor(call, activity.toolResults)
    const failed = result !== undefined && result.error !== null
    return {
      id: call.id,
      name: call.name,
      arguments: call.arguments,
      status: failed ? 'failed' : 'succeeded',
      error: result?.error ?? null,
      durationMs: activity.toolDurationsMs?.[call.id] ?? null,
      ...(result === undefined || failed ? {} : { output: result.result }),
    }
  })

export const rowsFromLive = (tools: readonly LiveToolCall[]): readonly ToolCallRow[] =>
  tools.map((tool) => ({
    id: tool.id,
    name: tool.name,
    arguments: tool.arguments,
    status: tool.status,
    error: tool.error,
    durationMs: tool.durationMs,
  }))
