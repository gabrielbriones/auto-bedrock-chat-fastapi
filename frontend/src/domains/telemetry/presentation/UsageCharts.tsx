import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { DailyUsageRow, ModelUsageRow } from '@/domains/telemetry/domain/public'
import { CHART_AXIS_TICK, CHART_CURSOR, CHART_GRID_STROKE, CHART_TOOLTIP_STYLE } from '@/components/ui/composed/chart-theme'
import { TELEMETRY_COPY } from '@/shared/copy/telemetry'
import { formatUsageNumber } from '@/domains/telemetry/presentation/format-usage'

const AXIS_LABEL_STYLE = { fill: 'var(--muted-foreground)', fontSize: 12, textAnchor: 'middle' } as const

type ModelDatum = {
  readonly model: string
  readonly input: number
  readonly output: number
  readonly total: number
  readonly turns: number
}

const toModelDatum = (row: ModelUsageRow, modelName: (modelId: string) => string): ModelDatum => ({
  model: modelName(row.modelId),
  input: row.tokens.input,
  output: row.tokens.output,
  total: row.tokens.total,
  turns: row.turnCount,
})

function ModelTooltip({ active, payload }: {
  readonly active: boolean | undefined
  readonly payload: ReadonlyArray<{ readonly payload?: ModelDatum }> | undefined
}) {
  const datum = payload?.[0]?.payload
  if (active !== true || datum === undefined) {
    return null
  }
  const lines = [
    [TELEMETRY_COPY.summary.input, datum.input],
    [TELEMETRY_COPY.summary.output, datum.output],
    [TELEMETRY_COPY.summary.totalTokens, datum.total],
    [TELEMETRY_COPY.summary.turns, datum.turns],
  ] as const

  return (
    <div className="grid gap-1 px-3 py-2" style={CHART_TOOLTIP_STYLE}>
      <p className="font-medium">{datum.model}</p>
      <dl className="grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5">
        {lines.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="text-right tabular-nums">{formatUsageNumber(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

// Horizontal bars: category labels get their own column, so none are skipped at narrow widths.
const MODEL_ROW_HEIGHT = 36
const MODEL_CHART_MIN_HEIGHT = 256
const LABEL_CHAR_WIDTH = 7
const LABEL_MAX_CHARS = 28

const truncateLabel = (label: string): string =>
  label.length > LABEL_MAX_CHARS ? `${label.slice(0, LABEL_MAX_CHARS - 1)}…` : label

export function ModelUsageChart({ rows, modelName }: {
  readonly rows: readonly ModelUsageRow[]
  readonly modelName: (modelId: string) => string
}) {
  const data = rows.map((row) => toModelDatum(row, modelName))
  const longest = Math.max(...data.map((entry) => Math.min(entry.model.length, LABEL_MAX_CHARS)))
  const height = Math.max(MODEL_CHART_MIN_HEIGHT, data.length * MODEL_ROW_HEIGHT + 72)

  return (
    <section aria-labelledby="usage-model-chart-title" className="grid gap-2">
      <h3 id="usage-model-chart-title" className="sr-only">
        {TELEMETRY_COPY.summary.chart}
      </h3>
      <p className="sr-only">{TELEMETRY_COPY.summary.chartTableDescription}</p>
      {/* Not `inert`: that would block the hover tooltip. The table carries the same data accessibly. */}
      <div aria-hidden="true" className="w-full" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart accessibilityLayer={false} layout="vertical" data={data} margin={{ top: 8, right: 24, bottom: 24, left: 24 }}>
            <CartesianGrid horizontal={false} stroke={CHART_GRID_STROKE} />
            <XAxis
              type="number"
              allowDecimals={false}
              tickFormatter={formatUsageNumber}
              label={{ value: TELEMETRY_COPY.summary.totalTokens, position: 'bottom', offset: 4, style: AXIS_LABEL_STYLE }}
              tick={CHART_AXIS_TICK}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="model"
              width={longest * LABEL_CHAR_WIDTH + 8}
              interval={0}
              tickFormatter={truncateLabel}
              label={{ value: TELEMETRY_COPY.summary.model, angle: -90, position: 'left', offset: 8, style: AXIS_LABEL_STYLE }}
              tick={CHART_AXIS_TICK}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip cursor={CHART_CURSOR} content={({ active, payload }) => <ModelTooltip active={active} payload={payload} />} />
            <Bar dataKey="total" name={TELEMETRY_COPY.summary.totalTokens} className="fill-primary" radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

export function DailyUsageChart({ rows }: { readonly rows: readonly DailyUsageRow[] }) {
  const data = rows.map((row) => ({
    date: row.date.toIso(),
    total: row.tokens.total,
  }))

  return (
    <section aria-labelledby="usage-daily-chart-title" className="grid gap-2">
      <h3 id="usage-daily-chart-title" className="sr-only">
        {TELEMETRY_COPY.byDay.chart}
      </h3>
      <p className="sr-only">{TELEMETRY_COPY.byDay.chartTableDescription}</p>
      <div aria-hidden="true" inert className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            <CartesianGrid vertical={false} stroke={CHART_GRID_STROKE} />
            <XAxis dataKey="date" tick={CHART_AXIS_TICK} axisLine={false} tickLine={false} />
            <YAxis allowDecimals={false} tick={CHART_AXIS_TICK} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={CHART_TOOLTIP_STYLE} cursor={CHART_CURSOR} />
            <Line type="monotone" dataKey="total" name="Total tokens" stroke="var(--primary)" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}