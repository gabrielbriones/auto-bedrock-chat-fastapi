import { useEffect, type ReactNode } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorState } from '@/components/ui/composed/error-state'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'

import { isRunActive } from '@/domains/knowledge/domain/public'
import { FileIngestForm } from '@/domains/knowledge/presentation/FileIngestForm'
import { KbSourceRunPanel } from '@/domains/knowledge/presentation/KbSourceRunPanel'
import { KbSourcesTable } from '@/domains/knowledge/presentation/KbSourcesTable'
import { WebCrawlForm } from '@/domains/knowledge/presentation/WebCrawlForm'

const SOURCES = KNOWLEDGE_COPY.sources

export type KbSourcesPageProps = {
  readonly title: string
}

function SectionCard({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle><h2>{title}</h2></CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">{children}</CardContent>
    </Card>
  )
}

// The legacy dashboard's KB Sources view: one run at a time, so the status panel is shared and
// both forms are held while a run is in flight; the ingested-sources list refreshes itself when a
// run finishes.
export function KbSourcesPage({ title }: KbSourcesPageProps) {
  const { kbSources } = useContainer()
  const snapshot = useContainerStore('kbSources')
  const busy = snapshot.submitting !== null || isRunActive(snapshot.run)

  useEffect(() => {
    void kbSources.load()
  }, [kbSources])

  const listError = snapshot.sourcesStatus === 'error' ? (
    <ErrorState description={SOURCES.list.loadError} onRetry={() => { void kbSources.loadSources() }} />
  ) : undefined

  return (
    <section className="grid gap-6 p-4">
      <h1 className="text-xl font-semibold">{title}</h1>
      <KbSourceRunPanel run={snapshot.run} problem={snapshot.runProblem} />
      <SectionCard title={SOURCES.list.title}>
        <KbSourcesTable
          rows={snapshot.sources}
          loading={snapshot.sourcesStatus === 'loading'}
          {...(listError === undefined ? {} : { error: listError })}
          deleting={snapshot.deleting}
          onDelete={(row) => { void kbSources.deleteSource(row) }}
        />
      </SectionCard>
      <SectionCard title={SOURCES.web.title}>
        <WebCrawlForm busy={busy} submitting={snapshot.submitting === 'web'} onSubmit={(request) => kbSources.startWebCrawl(request)} />
      </SectionCard>
      <SectionCard title={SOURCES.file.title}>
        <FileIngestForm busy={busy} submitting={snapshot.submitting === 'file'} onSubmit={(request) => kbSources.startFileIngest(request)} />
      </SectionCard>
    </section>
  )
}
