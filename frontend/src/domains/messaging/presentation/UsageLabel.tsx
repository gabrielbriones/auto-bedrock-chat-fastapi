import { Coins } from 'lucide-react'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { MESSAGING_COPY } from '@/shared/copy/messaging'
import { USAGE_PURPOSES } from '@/shared/ws/usage-frames'

import type { PurposeUsage, TurnUsage } from '@/domains/messaging/domain/turn'
import { formatEstimatedCost, formatTokenCount } from '@/domains/telemetry/domain/public'

const { usage: COPY } = MESSAGING_COPY

const rank = (purpose: PurposeUsage['purpose']): number => USAGE_PURPOSES.indexOf(purpose)

const purposeName = (purpose: PurposeUsage['purpose']): string => COPY.purposes[purpose]

const exact = (value: number): string => formatTokenCount(value, 'exact')

const estimate = (usage: TurnUsage): string | null =>
  usage.estimatedCost === null ? null : formatEstimatedCost(usage.estimatedCost, usage.currency)

// The price lives in the hover card; the label itself is always the token count.
const labelText = (usage: TurnUsage): string => {
  const label = COPY.tokens(formatTokenCount(usage.tokens.input), formatTokenCount(usage.tokens.output))
  return usage.final ? label : COPY.withPending(label)
}

const accessibleText = (usage: TurnUsage, purposes: readonly PurposeUsage[]): string => {
  const amount = estimate(usage)
  return COPY.readAll([
    amount === null ? COPY.unpriced : COPY.estimated(amount),
    COPY.readTokens(exact(usage.tokens.input), exact(usage.tokens.output)),
    ...purposes.map(({ purpose, tokens }) =>
      COPY.readPurpose(purposeName(purpose), exact(tokens.input), exact(tokens.output)),
    ),
    COPY.readCache(exact(usage.tokens.cacheRead), exact(usage.tokens.cacheWrite)),
    ...(usage.final ? [] : [COPY.pendingDetail]),
  ])
}

function Row({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div className="flex w-full justify-between gap-6">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  )
}

export function UsageLabel({ usage }: { readonly usage: TurnUsage | null }) {
  if (usage === null) {
    return null
  }

  const purposes = [...usage.byPurpose].sort((left, right) => rank(left.purpose) - rank(right.purpose))
  const amount = estimate(usage)

  // A popover rather than a tooltip: it uses the theme's popover surface and also opens on tap.
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={150}
        aria-label={accessibleText(usage, purposes)}
        className="ms-auto inline-flex max-w-full min-w-0 cursor-default items-center gap-1 rounded-sm text-xs text-muted-foreground transition-colors hover:text-foreground data-popup-open:text-foreground"
      >
        <Coins className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="truncate underline decoration-dotted underline-offset-2">{labelText(usage)}</span>
      </PopoverTrigger>
      <PopoverContent side="top" align="end" className="w-auto min-w-60 gap-2 text-xs">
        <p className="border-b border-border pb-2 text-sm font-medium">
          {amount === null ? COPY.unpriced : COPY.estimated(amount)}
        </p>
        <dl className="space-y-1">
          {purposes.map(({ purpose, tokens }) => (
            <Row
              key={purpose}
              label={purposeName(purpose)}
              value={COPY.inOut(exact(tokens.input), exact(tokens.output))}
            />
          ))}
          <Row label={COPY.cacheRead} value={exact(usage.tokens.cacheRead)} />
          <Row label={COPY.cacheWrite} value={exact(usage.tokens.cacheWrite)} />
        </dl>
        <dl className="border-t border-border pt-2 font-medium">
          <Row label={COPY.total} value={COPY.inOut(exact(usage.tokens.input), exact(usage.tokens.output))} />
        </dl>
        {usage.final ? null : <p className="text-muted-foreground">{COPY.pendingDetail}</p>}
      </PopoverContent>
    </Popover>
  )
}
