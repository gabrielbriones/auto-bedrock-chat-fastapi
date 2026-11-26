import type { Problem } from '@/shared/http/exception'
import { isErr, isOk, type Result } from '@/shared/kernel/result'
import { SKILLS_COPY } from '@/shared/copy/skills'

import type { SkillWriteError } from '@/domains/skills/application/ports'
import {
  CLOSED_EDITOR,
  createInitialSkillsSnapshot,
  type SkillsSnapshot,
  type SkillsStoreOptions,
} from '@/domains/skills/application/skills-snapshot'
import {
  contentOf,
  diffContent,
  toSummary,
  type Skill,
  type SkillContent,
  type SkillCreateRequest,
  type SkillSummary,
} from '@/domains/skills/domain/public'

export type { LoadStatus, SkillEditorMode, SkillsSnapshot, SkillsStoreOptions } from '@/domains/skills/application/skills-snapshot'

const COPY = SKILLS_COPY

const detailOf = (problem: Problem): { readonly description: string } | undefined =>
  problem.detail === undefined ? undefined : { description: problem.detail }

const byName = (a: SkillSummary, b: SkillSummary): number => a.name.localeCompare(b.name)

export class SkillsStore {
  readonly #options: SkillsStoreOptions
  readonly #listeners = new Set<() => void>()
  #listController: AbortController | null = null
  #detailController: AbortController | null = null
  #revisionsController: AbortController | null = null
  #snapshot: SkillsSnapshot = createInitialSkillsSnapshot()

  constructor(options: SkillsStoreOptions) {
    this.#options = options
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener)
    return () => {
      this.#listeners.delete(listener)
    }
  }

  getSnapshot = (): SkillsSnapshot => this.#snapshot

  async loadList(): Promise<void> {
    this.#listController?.abort()
    const controller = new AbortController()
    this.#listController = controller
    this.#patch({ listStatus: 'loading', listProblem: null })

    const result = await this.#options.gateway.list(controller.signal)
    if (controller.signal.aborted) return

    this.#patch(isErr(result)
      ? { listStatus: 'error', listProblem: result.error }
      : { listStatus: 'ready', skills: result.value.items, limits: result.value.limits })
  }

  // Optimistic: the switch flips at once and flips back if the server refuses.
  async setEnabled(name: string, enabled: boolean): Promise<void> {
    const before = this.#snapshot.skills.find((skill) => skill.name === name)
    if (before === undefined || this.#snapshot.toggling.includes(name)) return

    this.#patch({ toggling: [...this.#snapshot.toggling, name] })
    this.#updateRow(name, { enabled })
    const result = await this.#options.gateway.setEnabled(name, enabled)
    this.#patch({ toggling: this.#snapshot.toggling.filter((candidate) => candidate !== name) })

    if (isErr(result)) {
      this.#updateRow(name, { enabled: before.enabled })
      const message = enabled ? COPY.list.enableFailure(name) : COPY.list.disableFailure(name)
      this.#options.notifications.error(message, detailOf(result.error))
      return
    }
    this.#updateRow(name, toSummary(result.value))
    const { detail, conflict } = this.#snapshot
    this.#patch({
      detail: detail?.name === name ? { ...detail, enabled } : detail,
      conflict: conflict?.name === name ? { ...conflict, enabled } : conflict,
    })
  }

  openCreate(): void {
    this.#abortEditor()
    this.#patch({ ...CLOSED_EDITOR, editor: { mode: 'create' } })
  }

  async openEdit(name: string): Promise<void> {
    this.#abortEditor()
    this.#patch({ ...CLOSED_EDITOR, editor: { mode: 'edit', name }, detailStatus: 'loading' })
    const controller = new AbortController()
    this.#detailController = controller

    const [result] = await Promise.all([this.#options.gateway.get(name, controller.signal), this.#loadRevisions(name)])
    if (controller.signal.aborted) return

    this.#patch(isErr(result)
      ? { detailStatus: 'error', detailProblem: result.error }
      : { detailStatus: 'ready', detail: result.value })
  }

  async closeEditor(dirty: boolean): Promise<boolean> {
    if (dirty) {
      const discard = await this.#options.confirmations.confirm({
        title: COPY.editor.discardTitle,
        message: COPY.editor.discardMessage,
        confirmLabel: COPY.editor.discardConfirmLabel,
        tone: 'destructive',
      })
      if (!discard) return false
    }
    this.#abortEditor()
    this.#patch(CLOSED_EDITOR)
    return true
  }

  async create(request: SkillCreateRequest): Promise<boolean> {
    if (this.#snapshot.saving) return false
    this.#patch({ saving: true, saveProblem: null })
    const result = await this.#options.gateway.create(request)
    if (isErr(result)) {
      this.#patch({ saving: false, saveProblem: result.error })
      return false
    }

    const created = result.value
    this.#options.notifications.success(COPY.editor.createSuccess(created.name))
    this.#patch({
      ...CLOSED_EDITOR,
      skills: [...this.#snapshot.skills, toSummary(created)].sort(byName),
      editor: { mode: 'edit', name: created.name },
      detail: created,
      detailStatus: 'ready',
    })
    await this.#loadRevisions(created.name)
    return true
  }

  // After a conflict the changes are still the admin's own edits (against the version they opened),
  // applied on top of the stored revision: fields they did not touch keep the other admin's values.
  async save(content: SkillContent): Promise<boolean> {
    const { detail, conflict, saving } = this.#snapshot
    if (detail === null || saving) return false

    const changes = diffContent(contentOf(detail), content)
    if (Object.keys(changes).length === 0) {
      this.#options.notifications.info(COPY.editor.noChanges)
      return false
    }

    this.#patch({ saving: true, saveProblem: null })
    const base = conflict ?? detail
    const result = await this.#options.gateway.patch(base.name, base.revision, changes)
    return this.#applyWrite(result, (written) => COPY.editor.saveSuccess(written.revision), (problem) => {
      this.#patch({ saveProblem: problem })
    })
  }

  discardConflict(): void {
    const { conflict } = this.#snapshot
    if (conflict === null) return
    this.#patch({ detail: conflict, conflict: null, saveProblem: null })
  }

  async rollback(revision: number): Promise<boolean> {
    const { detail, conflict, saving } = this.#snapshot
    if (detail === null || saving) return false

    const confirmed = await this.#options.confirmations.confirm({
      title: COPY.revisions.rollbackConfirmTitle(revision),
      message: COPY.revisions.rollbackConfirmMessage(revision),
      confirmLabel: COPY.revisions.rollbackConfirmLabel,
    })
    if (!confirmed) return false

    this.#patch({ saving: true, saveProblem: null })
    const base = conflict ?? detail
    const result = await this.#options.gateway.rollback(base.name, revision, base.revision)
    return this.#applyWrite(result, (written) => COPY.revisions.rollbackSuccess(written.revision, revision), (problem) => {
      this.#options.notifications.error(COPY.revisions.rollbackFailure, detailOf(problem))
    })
  }

  async deleteSkill(name: string): Promise<boolean> {
    if (this.#snapshot.deleting !== null) return false
    const confirmed = await this.#options.confirmations.confirm({
      title: COPY.list.deleteConfirmTitle,
      message: COPY.list.deleteConfirmMessage(name),
      confirmLabel: COPY.list.deleteConfirmLabel,
      tone: 'destructive',
    })
    if (!confirmed) return false

    this.#patch({ deleting: name })
    const result = await this.#options.gateway.remove(name)
    this.#patch({ deleting: null })
    if (isErr(result)) {
      this.#options.notifications.error(COPY.list.deleteFailure, detailOf(result.error))
      return false
    }

    const { editor } = this.#snapshot
    if (editor.mode === 'edit' && editor.name === name) {
      this.#abortEditor()
      this.#patch(CLOSED_EDITOR)
    }
    this.#patch({ skills: this.#snapshot.skills.filter((skill) => skill.name !== name) })
    this.#options.notifications.success(COPY.list.deleteSuccess(name))
    return true
  }

  dispose(): void {
    this.#listController?.abort()
    this.#abortEditor()
    this.#listeners.clear()
  }

  #applyWrite(
    result: Result<Skill, SkillWriteError>,
    success: (written: Skill) => string,
    report: (problem: Problem) => void,
  ): boolean {
    this.#patch({ saving: false })
    if (isOk(result)) {
      const written = result.value
      this.#patch({ detail: written, conflict: null, saveProblem: null })
      this.#updateRow(written.name, toSummary(written))
      this.#options.notifications.success(success(written))
      void this.#loadRevisions(written.name)
      return true
    }
    if (result.error.kind === 'conflict') {
      const { current } = result.error
      this.#patch({ conflict: current })
      this.#updateRow(current.name, toSummary(current))
      this.#options.notifications.warning(COPY.conflict.toast)
      void this.#loadRevisions(current.name)
      return false
    }
    report(result.error.problem)
    return false
  }

  async #loadRevisions(name: string): Promise<void> {
    this.#revisionsController?.abort()
    const controller = new AbortController()
    this.#revisionsController = controller
    this.#patch({ revisionsStatus: 'loading' })

    const result = await this.#options.gateway.listRevisions(name, controller.signal)
    if (controller.signal.aborted) return
    this.#patch(isErr(result) ? { revisionsStatus: 'error' } : { revisionsStatus: 'ready', revisions: result.value })
  }

  #abortEditor(): void {
    this.#detailController?.abort()
    this.#revisionsController?.abort()
    this.#detailController = null
    this.#revisionsController = null
  }

  #updateRow(name: string, change: Partial<SkillSummary>): void {
    this.#patch({ skills: this.#snapshot.skills.map((skill) => (skill.name === name ? { ...skill, ...change } : skill)) })
  }

  #patch(change: Partial<SkillsSnapshot>): void {
    this.#snapshot = { ...this.#snapshot, ...change }
    for (const listener of this.#listeners) {
      listener()
    }
  }
}
