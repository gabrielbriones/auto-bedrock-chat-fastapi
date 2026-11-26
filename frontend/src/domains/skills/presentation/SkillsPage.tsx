import { useEffect, useId } from 'react'
import { PlusIcon, SearchIcon } from 'lucide-react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/composed/error-state'
import { DebouncedFilterInput } from '@/components/ui/composed/filter-bar'
import { Page, PageHeader } from '@/components/ui/composed/page-header'
import { Label } from '@/components/ui/label'
import { SKILLS_COPY } from '@/shared/copy/skills'

import { filterSkills } from '@/domains/skills/domain/public'
import { SkillEditorDrawer } from '@/domains/skills/presentation/SkillEditorDrawer'
import { SkillsTable } from '@/domains/skills/presentation/SkillsTable'
import { problemMessage } from '@/domains/skills/presentation/skill-messages'

const COPY = SKILLS_COPY

export type SkillsSearch = {
  readonly q?: string | undefined
}

export type SkillsPageProps = {
  readonly title: string
  readonly description?: string
  readonly search: SkillsSearch
  readonly onSearchChange: (patch: SkillsSearch) => void
}

function SkillsSearchField({ query, onCommit }: { readonly query: string; readonly onCommit: (value: string) => void }) {
  const id = useId()
  return (
    <div role="search" aria-label={COPY.list.search} className="relative w-full max-w-md">
      <Label htmlFor={id} className="sr-only">{COPY.list.search}</Label>
      <SearchIcon aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <DebouncedFilterInput id={id} type="search" className="pl-8" value={query} placeholder={COPY.list.searchPlaceholder} onCommit={onCommit} />
    </div>
  )
}

// The search filter is URL state; the list, the toggles and the open editor are the store's.
export function SkillsPage({ title, description, search, onSearchChange }: SkillsPageProps) {
  const { skills } = useContainer()
  const snapshot = useContainerStore('skills')
  const query = search.q ?? ''
  const rows = filterSkills(snapshot.skills, query)

  useEffect(() => {
    void skills.loadList()
  }, [skills])

  const listError = snapshot.listStatus === 'error' ? (
    <ErrorState
      description={snapshot.listProblem === null ? COPY.list.loadError : problemMessage(snapshot.listProblem)}
      onRetry={() => { void skills.loadList() }}
    />
  ) : undefined

  return (
    <Page>
      <PageHeader title={title} {...(description === undefined ? {} : { description })} />
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <SkillsSearchField query={query} onCommit={(value) => { onSearchChange({ q: value === '' ? undefined : value }) }} />
          <Button type="button" disabled={snapshot.listStatus !== 'ready'} onClick={() => { skills.openCreate() }}>
            <PlusIcon aria-hidden />
            {COPY.list.create}
          </Button>
        </div>
        <SkillsTable
          rows={rows}
          loading={snapshot.listStatus === 'loading' || snapshot.listStatus === 'idle'}
          {...(listError === undefined ? {} : { error: listError })}
          emptyTitle={query === '' ? COPY.list.empty : COPY.list.emptyFiltered}
          toggling={snapshot.toggling}
          deleting={snapshot.deleting}
          onOpen={(row) => { void skills.openEdit(row.name) }}
          onToggle={(row, enabled) => { void skills.setEnabled(row.name, enabled) }}
          onDelete={(row) => { void skills.deleteSkill(row.name) }}
        />
      </div>
      <SkillEditorDrawer />
    </Page>
  )
}
