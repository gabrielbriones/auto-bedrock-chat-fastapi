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
import { TELEMETRY_COPY } from '@/shared/copy/telemetry'

export function ModelUsageChart({ rows }: { readonly rows: readonly ModelUsageRow[] }) {
  const data = rows.map((row) => ({ model: row.modelId, total: row.tokens.total }))

  return (
    <section aria-labelledby="usage-model-chart-title" className="grid gap-2">
      <h3 id="usage-model-chart-title" className="sr-only">
        {TELEMETRY_COPY.summary.chart}
      </h3>
      <p className="sr-only">{TELEMETRY_COPY.summary.chartTableDescription}</p>
      <div aria-hidden="true" inert className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            <CartesianGrid vertical={false} strokeOpacity={0.2} />
            <XAxis dataKey="model" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Bar dataKey="total" name="Total tokens" className="fill-primary" radius={[4, 4, 0, 0]} />
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
            <CartesianGrid vertical={false} strokeOpacity={0.2} />
            <XAxis dataKey="date" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Line type="monotone" dataKey="total" name="Total tokens" stroke="var(--primary)" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}