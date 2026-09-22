import { useImperativeHandle, useRef, type ReactNode, type Ref } from 'react'

import { AdminDrawer } from '@/components/ui/composed/admin-drawer'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/composed/error-state'
import { LoadingState } from '@/components/ui/composed/loading-state'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'

import type { KnowledgeSnapshot } from '@/domains/knowledge/application/knowledge.store'
import type { SparsePatch } from '@/domains/knowledge/domain/public'
import { KbEditorForm, type KbEditorHandle } from '@/domains/knowledge/presentation/KbEditorForm'

export type { KbEditorHandle } from '@/domains/knowledge/presentation/KbEditorForm'

export type KbEditorDrawerProps = Pick<
  KnowledgeSnapshot,
  'activeDocument' | 'detailStatus' | 'detailProblem' | 'mutationPending' | 'saveProblem'
> & {
  readonly open: boolean
  readonly onClose: () => void
  readonly onSave: (patch: SparsePatch) => void
  readonly onResetCredibility: () => void
  readonly onRollback: () => void
  readonly onDelete: () => void
  readonly ref?: Ref<KbEditorHandle>
}

const Section = ({ title, children }: { readonly title: string; readonly children: ReactNode }) => (
  <section className="grid gap-2 border-t border-border py-4 first:border-t-0 first:pt-0">
    <h3 className="text-sm font-semibold">{title}</h3>
    {children}
  </section>
)

function CredibilitySummary({ score, removalFlagged, pending, onReset }: {
  readonly score: number
  readonly removalFlagged: boolean
  readonly pending: boolean
  readonly onReset: () => void
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <Badge variant="outline">{Math.round(score * 100)}%</Badge>
      {removalFlagged ? <Badge variant="destructive">{KNOWLEDGE_COPY.table.flagged}</Badge> : null}
      <Button type="button" variant="outline" size="sm" disabled={pending} onClick={onReset}>
        {KNOWLEDGE_COPY.editor.restoreScore}
      </Button>
    </div>
  )
}

function DangerActions({ pending, onRollback, onDelete }: {
  readonly pending: boolean
  readonly onRollback: () => void
  readonly onDelete: () => void
}) {
  return (
    <div className="flex gap-2">
      <Button type="button" variant="outline" disabled={pending} onClick={onRollback}>
        {KNOWLEDGE_COPY.editor.rollback}
      </Button>
      <Button type="button" variant="destructive" disabled={pending} onClick={onDelete}>
        {KNOWLEDGE_COPY.editor.delete}
      </Button>
    </div>
  )
}

// FIX-02 regression scope: this is the only place a KB field id is minted, and it comes from
// `useId()` inside `KbEditorForm` — never a hardcoded string a mounted filter could also claim.
export function KbEditorDrawer({ ref, ...props }: KbEditorDrawerProps) {
  const formRef = useRef<KbEditorHandle>(null)

  useImperativeHandle(ref, () => ({
    isDirty: () => formRef.current?.isDirty() ?? false,
  }))

  // FR-KB-020: closing (Escape/backdrop/close-button) always routes through `onClose`, which is a
  // `doc` search-param navigation on the knowledge-browser route — the page's `useBlocker` is the
  // single place that guards a dirty draft, so it isn't asked about twice.
  let content: ReactNode = null
  if (props.detailStatus === 'loading') {
    content = <LoadingState label={KNOWLEDGE_COPY.drawer.loading} />
  } else if (props.detailStatus === 'error') {
    content = <ErrorState description={KNOWLEDGE_COPY.drawer.loadError} />
  } else if (props.activeDocument !== null) {
    const document = props.activeDocument
    content = (
      <>
        <Section title={KNOWLEDGE_COPY.table.credibility}>
          <CredibilitySummary
            score={document.credibility.score}
            removalFlagged={document.credibility.removalFlagged}
            pending={props.mutationPending}
            onReset={props.onResetCredibility}
          />
        </Section>
        <KbEditorForm
          key={document.id}
          ref={formRef}
          document={document}
          pending={props.mutationPending}
          problem={props.saveProblem}
          onSave={props.onSave}
        />
        <DangerActions pending={props.mutationPending} onRollback={props.onRollback} onDelete={props.onDelete} />
      </>
    )
  }

  return (
    <AdminDrawer
      open={props.open}
      onOpenChange={(open) => { if (!open) props.onClose() }}
      title={KNOWLEDGE_COPY.drawer.title}
      className="data-[side=right]:w-full data-[side=right]:sm:max-w-none data-[side=right]:md:w-3/5 data-[side=right]:md:max-w-6xl"
    >
      {content}
    </AdminDrawer>
  )
}
