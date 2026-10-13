import type { CSSProperties } from 'react'

// Recharts paints its own tooltip and axis text with hard-coded greys and a white card, which
// read as a hole in the page on the navy dark theme. Every chart takes its colours from the
// semantic tokens instead, so the charts follow the theme like everything else does.
export const CHART_TOOLTIP_STYLE: CSSProperties = {
  background: 'var(--popover)',
  color: 'var(--popover-foreground)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  boxShadow: 'var(--shadow-md)',
  fontSize: '0.8125rem',
}

export const CHART_CURSOR = { fill: 'var(--accent)', stroke: 'var(--border)' } as const

export const CHART_GRID_STROKE = 'var(--border)'

export const CHART_AXIS_TICK = { fill: 'var(--muted-foreground)', fontSize: 12 } as const
