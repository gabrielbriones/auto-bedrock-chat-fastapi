import { CheckIcon, ChevronRightIcon, Loader2Icon, WrenchIcon, XIcon } from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'
import type { LiveToolStatus } from '@/domains/messaging/domain/turn'
import { CodeBlock } from '@/domains/messaging/presentation/CodeBlock'
import {
  formatDuration,
  formatValue,
  summarizeArguments,
  type ToolCallRow,
} from '@/domains/messaging/presentation/tool-call-rows'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

const { transcript: COPY } = MESSAGING_COPY

/** A finished answer with more calls than this folds them under one summary row. */
const GROUP_ABOVE = 5

const STATUS_ICON: Record<LiveToolStatus, ReactNode> = {
  running: <Loader2Icon aria-hidden="true" className="size-4 shrink-0 animate-spin text-primary" />,
  succeeded: <CheckIcon aria-hidden="true" className="size-4 shrink-0 text-success-600 dark:text-success-400" />,
  failed: <XIcon aria-hidden="true" className="size-4 shrink-0 text-destructive" />,
}

const STATUS_VERB: Record<LiveToolStatus, string> = {
  running: COPY.toolRunning,
  succeeded: COPY.toolRan,
  failed: COPY.toolFailed,
}

type ToolCallGroup = { readonly key: string; readonly rows: readonly ToolCallRow[] }

/** Consecutive calls to the same tool share one leading icon joined by a vertical rail. */
function groupConsecutive(rows: readonly ToolCallRow[]): ToolCallGroup[] {
  const groups: { key: string; rows: ToolCallRow[] }[] = []
  rows.forEach((row, index) => {
    const last = groups.at(-1)
    if (last !== undefined && last.rows[0]?.name === row.name) {
      last.rows.push(row)
    } else {
      groups.push({ key: `${row.id}:${index}`, rows: [row] })
    }
  })
  return groups
}

function ToolCallItem({ row, showIcon }: { readonly row: ToolCallRow; readonly showIcon: boolean }) {
  // Payloads can be megabytes of JSON; highlighting them is deferred until a row is opened.
  const [open, setOpen] = useState(false)
  const summary = summarizeArguments(row.arguments)
  const failed = row.status === 'failed'

  return (
    <li>
      <details className="group/tool" onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary
          className="flex cursor-pointer list-none items-center gap-2 rounded-md px-2 py-1 group-open/tool:bg-foreground/5 hover:bg-muted [&::-webkit-details-marker]:hidden"
        >
          {showIcon ? (
            <WrenchIcon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
          ) : (
            <span aria-hidden="true" className="size-4 shrink-0" />
          )}
          <span className="shrink-0 text-muted-foreground">{STATUS_VERB[row.status]}</span>
          <code className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{row.name}</code>
          {summary !== '' ? <span className="min-w-0 truncate text-muted-foreground">{summary}</span> : null}
          <span className="ms-auto flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            {row.durationMs !== null ? formatDuration(row.durationMs) : null}
            {STATUS_ICON[row.status]}
            <ChevronRightIcon aria-hidden="true" className="size-4 transition-transform group-open/tool:rotate-90" />
          </span>
        </summary>
        {open ? (
          <div className="ms-8 me-2 mt-1 mb-3 space-y-1.5 rounded-lg border border-border bg-tool-panel-bg p-3 shadow-sm dark:border-white/15">
            <p className="text-[0.625rem] font-semibold tracking-wider text-muted-foreground/80 uppercase">{COPY.toolInput}</p>
            <CodeBlock code={formatValue(row.arguments)} language="json" wrap className="my-0 dark:bg-background/70" />
            {failed ? (
              <>
                <p className="pt-1.5 text-[0.625rem] font-semibold tracking-wider text-destructive/90 uppercase">{COPY.toolError}</p>
                <CodeBlock code={formatValue(row.error)} language="json" wrap className="my-0 dark:bg-background/70" />
              </>
            ) : row.output !== undefined ? (
              <>
                <p className="pt-1.5 text-[0.625rem] font-semibold tracking-wider text-muted-foreground/80 uppercase">{COPY.toolOutput}</p>
                <CodeBlock code={formatValue(row.output)} language="json" wrap className="my-0 dark:bg-background/70" />
              </>
            ) : row.status === 'running' ? null : (
              <p className="text-xs text-muted-foreground">{COPY.toolOutputPending}</p>
            )}
          </div>
        ) : null}
      </details>
    </li>
  )
}

export type ToolCallListProps = {
  readonly rows: readonly ToolCallRow[]
  /** In-flight lists always stay expanded so the user can see what is happening. */
  readonly live?: boolean
  readonly className?: string
}

/** VS Code-style tool activity: one row per call with its status, name and inline arguments. */
export function ToolCallList({ rows, live = false, className }: ToolCallListProps) {
  if (rows.length === 0) {
    return null
  }

  const list = (
    <ul aria-label={COPY.toolCalls} className="space-y-0.5">
      {groupConsecutive(rows).map((group) => (
        <li key={group.key} className="relative">
          {group.rows.length > 1 ? (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute start-4 top-6 bottom-3.5 w-px -translate-x-1/2 rounded-full bg-muted-foreground/70"
            />
          ) : null}
          <ul className="space-y-0.5">
            {group.rows.map((row, index) => (
              <ToolCallItem key={`${row.id}:${index}`} row={row} showIcon={index === 0} />
            ))}
          </ul>
        </li>
      ))}
    </ul>
  )

  if (live || rows.length <= GROUP_ABOVE) {
    return <div className={cn('text-sm', className)}>{list}</div>
  }

  const failed = rows.filter((row) => row.status === 'failed').length

  return (
    <details className={cn('group/tools text-sm', className)}>
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-md px-2 py-1 hover:bg-muted [&::-webkit-details-marker]:hidden">
        <WrenchIcon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
        <span className="text-muted-foreground">{COPY.toolsUsed(rows.length, failed)}</span>
        <ChevronRightIcon
          aria-hidden="true"
          className="ms-auto size-4 text-muted-foreground transition-transform group-open/tools:rotate-90"
        />
      </summary>
      <div className="mt-1 ps-4">{list}</div>
    </details>
  )
}
