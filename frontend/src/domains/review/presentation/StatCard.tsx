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
    <Card size="sm">
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl">{value.toLocaleString()}</CardTitle>
      </CardHeader>
      {hint === undefined ? null : (
        <CardContent className="text-xs text-muted-foreground">{hint}</CardContent>
      )}
    </Card>
  )
}
