import { Gauge } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

import type { SessionUsage } from '@/domains/messaging/domain/session-usage'
import type { ContextFill } from '@/domains/messaging/domain/turn'
import { formatEstimatedCost, formatTokenCount } from '@/domains/telemetry/domain/public'

const { session: COPY, usage: USAGE_COPY } = MESSAGING_COPY

const exact = (value: number): string => formatTokenCount(value, 'exact')

const percentOf = ({ usedTokens, maxTokens }: ContextFill): number => Math.min(100, (usedTokens / maxTokens) * 100)

const percentText = (percent: number): string => (percent > 0 && percent < 1 ? '<1%' : `${Math.round(percent)}%`)

const meterClass = (percent: number): string =>
  percent >= 90 ? 'bg-destructive' : percent >= 75 ? 'bg-warning-500' : 'bg-primary'

const toneClass = (percent: number): string | false =>
  percent >= 90 ? 'text-destructive' : percent >= 75 && 'text-warning-600 dark:text-warning-400'

const estimateText = (usage: SessionUsage): string =>
  usage.estimatedCost === null ? COPY.noEstimate : USAGE_COPY.estimated(formatEstimatedCost(usage.estimatedCost, usage.currency))

const accessibleText = (usage: SessionUsage): string =>
  COPY.read([
    estimateText(usage),
    USAGE_COPY.readTokens(exact(usage.tokens.input), exact(usage.tokens.output)),
    ...(usage.context === null
      ? []
      : [`${COPY.context}: ${COPY.contextUsed(exact(usage.context.usedTokens), exact(usage.context.maxTokens))}`]),
    ...(usage.unpricedAnswers > 0 && usage.estimatedCost !== null ? [COPY.unpriced(usage.unpricedAnswers)] : []),
    ...(usage.pending ? [USAGE_COPY.pendingDetail] : []),
  ])

function ContextMeter({ context }: { readonly context: ContextFill }) {
  const percent = percentOf(context)
  return (
    <div className="space-y-1">
      <div className="flex justify-between gap-6">
        <span className="text-muted-foreground">{COPY.context}</span>
        <span className="tabular-nums">{percentText(percent)}</span>
      </div>
      <div
        role="meter"
        aria-label={COPY.contextMeter}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
        className="h-1.5 overflow-hidden rounded-full bg-muted"
      >
        <div className={cn('h-full rounded-full', meterClass(percent))} style={{ width: `${percent}%` }} />
      </div>
      <p className="text-muted-foreground tabular-nums">
        {COPY.contextUsed(exact(context.usedTokens), exact(context.maxTokens))}
      </p>
    </div>
  )
}

function Row({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div className="flex w-full justify-between gap-6">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  )
}

// Renders nothing until an answer in this conversation has reported usage.
export function SessionUsageSummary({ usage }: { readonly usage: SessionUsage | null }) {
  if (usage === null) {
    return null
  }

  const percent = usage.context === null ? null : percentOf(usage.context)
  const headline =
    usage.estimatedCost === null
      ? COPY.tokens(formatTokenCount(usage.tokens.total))
      : formatEstimatedCost(usage.estimatedCost, usage.currency)

  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={150}
        aria-label={accessibleText(usage)}
        className="ms-auto inline-flex min-w-0 shrink-0 cursor-default items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground data-popup-open:text-foreground"
      >
        <Gauge className="size-3.5 shrink-0" aria-hidden="true" />
        {percent === null ? null : (
          <span className={cn('tabular-nums', toneClass(percent))}>
            {COPY.contextPercent(percentText(percent))}
          </span>
        )}
        <span className="truncate tabular-nums">{headline}</span>
      </PopoverTrigger>
      <PopoverContent side="top" align="end" className="w-auto min-w-64 gap-2 text-xs">
        <p className="border-b border-border pb-2 text-sm font-medium">{COPY.title}</p>
        <p className="text-sm">{estimateText(usage)}</p>
        <dl className="space-y-1">
          <Row label={USAGE_COPY.total} value={USAGE_COPY.inOut(exact(usage.tokens.input), exact(usage.tokens.output))} />
          <Row label={USAGE_COPY.cacheRead} value={exact(usage.tokens.cacheRead)} />
          <Row label={USAGE_COPY.cacheWrite} value={exact(usage.tokens.cacheWrite)} />
          <Row label={COPY.answers} value={String(usage.answers)} />
        </dl>
        {usage.context === null ? null : (
          <div className="border-t border-border pt-2">
            <ContextMeter context={usage.context} />
          </div>
        )}
        {usage.unpricedAnswers > 0 && usage.estimatedCost !== null ? (
          <p className="text-muted-foreground">{COPY.unpriced(usage.unpricedAnswers)}</p>
        ) : null}
        {usage.pending ? <p className="text-muted-foreground">{USAGE_COPY.pendingDetail}</p> : null}
      </PopoverContent>
    </Popover>
  )
}
