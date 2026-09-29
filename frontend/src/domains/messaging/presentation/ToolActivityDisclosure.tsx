import { ChevronDownIcon } from 'lucide-react'
import { useState } from 'react'

import type { TurnActivity, ToolCall, ToolResult } from '@/domains/messaging/domain/turn'
import { redactSensitive } from '@/domains/messaging/presentation/redaction'
import { CodeBlock } from '@/domains/messaging/presentation/CodeBlock'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

export type ToolActivityDisclosureProps = {
  readonly activity: TurnActivity
}

const formatValue = (value: unknown): string => {
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

const resultFor = (call: ToolCall, results: readonly ToolResult[]): ToolResult | undefined =>
  results.find((result) => result.toolCallId === call.id) ?? results.find((result) => result.name === call.name)

/** A native disclosure keeps tool provenance collapsed initially and fully keyboard-operable. */
export function ToolActivityDisclosure({ activity }: ToolActivityDisclosureProps) {
  // Payloads can be megabytes of JSON per turn; highlighting them is deferred until asked for.
  const [open, setOpen] = useState(false)

  if (activity.toolCalls.length === 0 && activity.toolResults.length === 0) {
    return null
  }

  const toolNames = activity.toolCalls.map((call) => call.name).join(', ') || MESSAGING_COPY.transcript.toolResults

  return (
    <details
      className="max-w-prose border-t border-border pt-3 text-sm"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 font-medium">
        <ChevronDownIcon aria-hidden="true" className="size-4" />
        <span>{MESSAGING_COPY.transcript.toolActivity(toolNames)}</span>
      </summary>
      {open ? (
        <div className="mt-3 space-y-4">
          {activity.toolCalls.map((call) => {
            const result = resultFor(call, activity.toolResults)
            return (
              <section aria-label={call.name} key={call.id}>
                <h3 className="text-sm font-medium">{call.name}</h3>
                <p className="mt-1 text-muted-foreground">
                  {MESSAGING_COPY.transcript.status}:{' '}
                  {result === undefined || result.error === null
                    ? MESSAGING_COPY.transcript.completed
                    : MESSAGING_COPY.transcript.failed}
                </p>
                <p className="mt-1 text-muted-foreground">{MESSAGING_COPY.transcript.arguments}</p>
                <CodeBlock code={formatValue(call.arguments)} language="json" />
                {result !== undefined ? (
                  <>
                    <p className="mt-1 text-muted-foreground">
                      {result.error === null ? MESSAGING_COPY.transcript.result : MESSAGING_COPY.transcript.toolError}
                    </p>
                    <CodeBlock code={formatValue(result.error ?? result.result)} language="json" />
                  </>
                ) : null}
              </section>
            )
          })}
        </div>
      ) : null}
    </details>
  )
}