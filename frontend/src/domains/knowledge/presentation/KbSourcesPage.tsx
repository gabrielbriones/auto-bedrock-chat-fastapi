import { useEffect, type ReactNode } from 'react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorState } from '@/components/ui/composed/error-state'
import { OffsetPagination } from '@/components/ui/composed/offset-pagination'
import { Page, PageHeader } from '@/components/ui/composed/page-header'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import { offsetWindow } from '@/shared/kernel/pagination'

import { isRunActive, type KbSourceFilter } from '@/domains/knowledge/domain/public'
import { FileIngestForm } from '@/domains/knowledge/presentation/FileIngestForm'
import { KbSourceRunPanel } from '@/domains/knowledge/presentation/KbSourceRunPanel'
import { KbSourcesTable } from '@/domains/knowledge/presentation/KbSourcesTable'
import { WebCrawlForm } from '@/domains/knowledge/presentation/WebCrawlForm'

const SOURCES = KNOWLEDGE_COPY.sources

export type KbSourcesPageProps = {
  readonly title: string
  readonly description?: string
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

function SourceTypeFilter({ value, onChange }: { readonly value: KbSourceFilter; readonly onChange: (value: KbSourceFilter) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      {SOURCES.list.sourceType}
      <select
        aria-label={SOURCES.list.sourceType}
        value={value ?? ''}
        onChange={(event) => { onChange(event.target.value === '' ? null : event.target.value as Exclude<KbSourceFilter, null>) }}
        className="rounded-md border border-input bg-background px-3 py-2"
      >
        <option value="">{SOURCES.list.allTypes}</option>
        <option value="web">{SOURCES.list.type.web}</option>
        <option value="file">{SOURCES.list.type.file}</option>
        <option value="feedback">{SOURCES.list.type.feedback}</option>
      </select>
    </label>
  )
}

// The legacy dashboard's KB Sources view: one run at a time, so the status panel is shared and
// both forms are held while a run is in flight; the ingested-sources list refreshes itself when a
// run finishes.
export function KbSourcesPage({ title, description }: KbSourcesPageProps) {
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
    <Page>
      <PageHeader title={title} {...(description === undefined ? {} : { description })} />
      <KbSourceRunPanel run={snapshot.run} problem={snapshot.runProblem} />
      <SectionCard title={SOURCES.list.title}>
        <SourceTypeFilter value={snapshot.sourceType} onChange={(value) => { void kbSources.setSourceType(value) }} />
        <KbSourcesTable
          rows={snapshot.sources}
          loading={snapshot.sourcesStatus === 'loading'}
          {...(listError === undefined ? {} : { error: listError })}
          deleting={snapshot.deleting}
          onDelete={(row) => { void kbSources.deleteSource(row) }}
        />
        <OffsetPagination
          range={offsetWindow(snapshot.sourcePage, snapshot.sourceTotal)}
          onNavigate={(offset) => { void kbSources.setSourceOffset(offset) }}
          label={SOURCES.list.caption}
        />
      </SectionCard>
      <SectionCard title={SOURCES.web.title}>
        <WebCrawlForm busy={busy} submitting={snapshot.submitting === 'web'} onSubmit={(request) => kbSources.startWebCrawl(request)} />
      </SectionCard>
      <SectionCard title={SOURCES.file.title}>
        <FileIngestForm busy={busy} submitting={snapshot.submitting === 'file'} onSubmit={(request) => kbSources.startFileIngest(request)} />
      </SectionCard>
    </Page>
  )
}
