import { useRef, type ReactNode, type RefObject } from 'react'
import { Trash2Icon } from 'lucide-react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { AdminDrawer } from '@/components/ui/composed/admin-drawer'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { SKILLS_COPY } from '@/shared/copy/skills'

import type { SkillsSnapshot } from '@/domains/skills/application/skills.store'
import { SkillEditorForm, type SkillEditorHandle } from '@/domains/skills/presentation/SkillEditorForm'
import { SkillRevisionsList } from '@/domains/skills/presentation/SkillRevisionsList'
import { problemMessage } from '@/domains/skills/presentation/skill-messages'

const COPY = SKILLS_COPY.editor

const titleOf = (snapshot: SkillsSnapshot): string => {
  switch (snapshot.editor.mode) {
    case 'edit':
      return COPY.editTitle(snapshot.detail?.displayName ?? snapshot.editor.name)
    case 'create':
    case 'closed':
      return COPY.createTitle
  }
}

function EditBody({ snapshot, formRef }: { readonly snapshot: SkillsSnapshot; readonly formRef: RefObject<SkillEditorHandle | null> }) {
  const { skills } = useContainer()
  const { detail } = snapshot

  if (snapshot.detailStatus === 'loading') return <LoadingState label={COPY.loading} />
  if (detail === null) {
    const description = snapshot.detailProblem === null ? COPY.loadError : problemMessage(snapshot.detailProblem)
    return <ErrorState description={description} />
  }

  const busy = snapshot.saving || snapshot.deleting !== null
  return (
    <div className="grid gap-6">
      <SkillEditorForm
        // A new stored revision (save, rollback, adopting the other admin's version) restarts the draft.
        key={`${detail.name}:${detail.revision}`}
        ref={formRef}
        skill={detail}
        limits={snapshot.limits}
        saving={snapshot.saving}
        problem={snapshot.saveProblem}
        conflict={snapshot.conflict}
        onSubmit={(request) => { void skills.save(request) }}
        onDiscardConflict={() => { skills.discardConflict() }}
      />
      <SkillRevisionsList
        revisions={snapshot.revisions}
        status={snapshot.revisionsStatus}
        currentRevision={(snapshot.conflict ?? detail).revision}
        disabled={busy}
        onRollback={(revision) => { void skills.rollback(revision) }}
      />
      <div className="border-t border-border pt-4">
        <Button type="button" variant="destructive" disabled={busy} onClick={() => { void skills.deleteSkill(detail.name) }}>
          <Trash2Icon aria-hidden />
          {COPY.delete}
        </Button>
      </div>
    </div>
  )
}

export function SkillEditorDrawer() {
  const { skills } = useContainer()
  const snapshot = useContainerStore('skills')
  const formRef = useRef<SkillEditorHandle>(null)
  const { mode } = snapshot.editor

  let content: ReactNode = null
  if (mode === 'create') {
    content = (
      <SkillEditorForm
        ref={formRef}
        skill={null}
        limits={snapshot.limits}
        saving={snapshot.saving}
        problem={snapshot.saveProblem}
        conflict={null}
        onSubmit={(request) => { void skills.create(request) }}
      />
    )
  } else if (mode === 'edit') {
    content = <EditBody snapshot={snapshot} formRef={formRef} />
  }

  return (
    <AdminDrawer
      open={mode !== 'closed'}
      onOpenChange={(open) => { if (!open) void skills.closeEditor(formRef.current?.isDirty() ?? false) }}
      title={titleOf(snapshot)}
      className="data-[side=right]:w-full data-[side=right]:sm:max-w-none data-[side=right]:md:w-4/5 data-[side=right]:md:max-w-6xl"
    >
      {content}
    </AdminDrawer>
  )
}
