import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export type StatCardProps = {
  readonly label: string
  readonly value: number
  readonly hint?: string
}

// SPEC-016 §4: one card per stat, so the grid layout is the only thing that changes if the count
// of cards changes.
export function StatCard({ label, value, hint }: StatCardProps) {
  return (
    <Card size="sm" className="relative border-t-2 border-t-primary/70 shadow-sm">
      <CardHeader>
        <CardDescription className="text-xs font-medium tracking-wide uppercase">{label}</CardDescription>
        <CardTitle className="text-3xl font-semibold tracking-tight tabular-nums">{value.toLocaleString()}</CardTitle>
      </CardHeader>
      {hint === undefined ? null : (
        <CardContent className="text-xs text-muted-foreground">{hint}</CardContent>
      )}
    </Card>
  )
}
