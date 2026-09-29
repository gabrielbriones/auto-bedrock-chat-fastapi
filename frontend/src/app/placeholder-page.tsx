// TEMPORARY (XMGPLAT-11383 Phase 1): stands in for every page until its bounded context lands
// under src/domains/<ctx>/presentation/. Each route drops its use the moment it has a real page.
export type PlaceholderPageProps = {
  readonly title: string
}

export function PlaceholderPage({ title }: PlaceholderPageProps) {
  return (
    <section className="p-8">
      <h1 className="text-lg font-medium text-foreground">{title}</h1>
    </section>
  )
}
