import { useEffect, useRef, type RefObject } from 'react'
import { useBlocker } from '@tanstack/react-router'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Checkbox } from '@/components/ui/checkbox'
import { DebouncedFilterInput, FilterBar, FilterField } from '@/components/ui/composed/filter-bar'
import { ErrorState } from '@/components/ui/composed/error-state'
import { OffsetPagination } from '@/components/ui/composed/offset-pagination'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import { kbDocumentId, type KbDocumentId } from '@/shared/kernel/branded'
import { offsetWindow } from '@/shared/kernel/pagination'

import { toKnowledgeQuery, type KnowledgeSearchInput } from '@/domains/knowledge/application/knowledge-query'
import type { SparsePatch } from '@/domains/knowledge/domain/public'
import { KnowledgeTable } from '@/domains/knowledge/presentation/KnowledgeTable'
import { KbEditorDrawer, type KbEditorHandle } from '@/domains/knowledge/presentation/KbEditorDrawer'

export type KnowledgeBrowserSearch = KnowledgeSearchInput & {
  readonly doc?: string | undefined
}

export type KnowledgeBrowserPatch = {
  readonly source?: string | undefined
  readonly topic?: string | undefined
  readonly tags?: string | undefined
  readonly from?: string | undefined
  readonly to?: string | undefined
  readonly flagged?: boolean | undefined
  readonly offset?: number | undefined
  readonly doc?: string | undefined
}

export type KnowledgeBrowserPageProps = {
  readonly title: string
  readonly search: KnowledgeBrowserSearch
  readonly onSearchChange: (patch: KnowledgeBrowserPatch) => void
}

function KnowledgeFilters({ search, onSearchChange }: Pick<KnowledgeBrowserPageProps, 'search' | 'onSearchChange'>) {
  const reset = () => onSearchChange({
    source: undefined,
    topic: undefined,
    tags: undefined,
    from: undefined,
    to: undefined,
    flagged: false,
    offset: 0,
  })

  return (
    <FilterBar onReset={reset}>
      <FilterField label={KNOWLEDGE_COPY.filters.source}>
        {(id) => <DebouncedFilterInput key={search.source ?? ''} id={id} value={search.source ?? ''} type="search" onCommit={(source) => onSearchChange({ source: source || undefined, offset: 0 })} />}
      </FilterField>
      <FilterField label={KNOWLEDGE_COPY.filters.topic}>
        {(id) => <DebouncedFilterInput key={search.topic ?? ''} id={id} value={search.topic ?? ''} onCommit={(topic) => onSearchChange({ topic: topic || undefined, offset: 0 })} />}
      </FilterField>
      <FilterField label={KNOWLEDGE_COPY.filters.tags}>
        {(id) => <DebouncedFilterInput key={search.tags ?? ''} id={id} value={search.tags ?? ''} onCommit={(tags) => onSearchChange({ tags: tags || undefined, offset: 0 })} />}
      </FilterField>
      <FilterField label={KNOWLEDGE_COPY.filters.from}>
        {(id) => <DebouncedFilterInput key={search.from ?? ''} id={id} value={search.from ?? ''} type="date" onCommit={(from) => onSearchChange({ from: from || undefined, offset: 0 })} />}
      </FilterField>
      <FilterField label={KNOWLEDGE_COPY.filters.to}>
        {(id) => <DebouncedFilterInput key={search.to ?? ''} id={id} value={search.to ?? ''} type="date" onCommit={(to) => onSearchChange({ to: to || undefined, offset: 0 })} />}
      </FilterField>
      <FilterField label={KNOWLEDGE_COPY.filters.flagged}>
        {(id) => <Checkbox id={id} checked={search.flagged} onCheckedChange={(checked) => onSearchChange({ flagged: checked === true, offset: 0 })} />}
      </FilterField>
    </FilterBar>
  )
}

function useKnowledgeList(search: KnowledgeBrowserSearch) {
  const { knowledge } = useContainer()
  const snapshot = useContainerStore('knowledge')
  const { source, topic, tags, from, to, flagged, offset, doc } = search

  useEffect(() => {
    void knowledge.load(toKnowledgeQuery({ source, topic, tags, from, to, flagged, offset }))
  }, [knowledge, source, topic, tags, from, to, flagged, offset])

  useEffect(() => {
    if (doc === undefined) {
      knowledge.close()
    } else {
      void knowledge.open(kbDocumentId(doc))
    }
  }, [knowledge, doc])

  return { knowledge, snapshot }
}

// FR-KB-020: one blocker guards both leaving the route entirely and closing the drawer, since a
// drawer close is itself a search-param navigation (`doc` cleared) that goes through the router.
function useUnsavedChangesGuard(drawerRef: RefObject<KbEditorHandle | null>) {
  const { confirmations } = useContainer()

  useBlocker({
    enableBeforeUnload: () => drawerRef.current?.isDirty() === true,
    shouldBlockFn: async () => {
      if (drawerRef.current?.isDirty() !== true) {
        return false
      }
      const discard = await confirmations.confirm({
        title: KNOWLEDGE_COPY.editor.discardTitle,
        message: KNOWLEDGE_COPY.editor.discardMessage,
        confirmLabel: KNOWLEDGE_COPY.editor.discardConfirmLabel,
        tone: 'destructive',
      })
      return !discard
    },
  })
}

function useKnowledgeActions(
  knowledge: ReturnType<typeof useKnowledgeList>['knowledge'],
  activeId: KbDocumentId | null,
  onSearchChange: KnowledgeBrowserPageProps['onSearchChange'],
) {
  return {
    save: (patch: SparsePatch) => {
      if (activeId !== null) void knowledge.save(activeId, patch)
    },
    resetCredibility: () => {
      if (activeId !== null) void knowledge.resetCredibility(activeId)
    },
    rollback: async () => {
      if (activeId === null) return
      const succeeded = await knowledge.rollback(activeId)
      if (succeeded) {
        onSearchChange({ doc: undefined })
      }
    },
    remove: async () => {
      if (activeId === null) return
      const previousOffset = await knowledge.remove(activeId)
      onSearchChange(previousOffset === null ? { doc: undefined } : { doc: undefined, offset: previousOffset })
    },
  }
}

function KnowledgeBrowserPageContent({ title, search, onSearchChange }: KnowledgeBrowserPageProps) {
  const { knowledge, snapshot } = useKnowledgeList(search)
  const drawerRef = useRef<KbEditorHandle>(null)
  const activeId = snapshot.activeDocument?.id ?? null
  const actions = useKnowledgeActions(knowledge, activeId, onSearchChange)
  useUnsavedChangesGuard(drawerRef)

  const error = snapshot.listStatus === 'error' ? (
    <ErrorState description={KNOWLEDGE_COPY.browser.loadError} onRetry={() => { void knowledge.load(toKnowledgeQuery(search)) }} />
  ) : undefined

  return (
    <section className="grid gap-4 p-4">
      <h1 className="text-xl font-semibold">{title}</h1>
      <KnowledgeFilters search={search} onSearchChange={onSearchChange} />
      <KnowledgeTable
        rows={snapshot.page.items}
        loading={snapshot.listStatus === 'loading'}
        {...(error === undefined ? {} : { error })}
        onOpen={(document) => onSearchChange({ doc: document.id })}
      />
      <OffsetPagination range={offsetWindow(snapshot.page, snapshot.page.total)} onNavigate={(offset) => onSearchChange({ offset })} />
      <KbEditorDrawer
        ref={drawerRef}
        open={search.doc !== undefined}
        activeDocument={snapshot.activeDocument}
        detailStatus={snapshot.detailStatus}
        detailProblem={snapshot.detailProblem}
        mutationPending={snapshot.mutationPending}
        saveProblem={snapshot.saveProblem}
        onClose={() => onSearchChange({ doc: undefined })}
        onSave={actions.save}
        onResetCredibility={actions.resetCredibility}
        onRollback={actions.rollback}
        onDelete={() => { void actions.remove() }}
      />
    </section>
  )
}

export function KnowledgeBrowserPage(props: KnowledgeBrowserPageProps) {
  return <KnowledgeBrowserPageContent {...props} />
}