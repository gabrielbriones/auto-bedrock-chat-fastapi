import { describe, expect, it, jest } from '@jest/globals'

import type { Problem } from '@/shared/http/exception'
import { SKILLS_COPY } from '@/shared/copy/skills'
import { err, ok } from '@/shared/kernel/result'

import type { SkillsGateway } from '@/domains/skills/application/ports'
import { SkillsStore } from '@/domains/skills/application/skills.store'
import { contentOf, toSummary, type Skill, type SkillLimits } from '@/domains/skills/domain/public'

import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'
import { ScriptedConfirmationPort } from '../../../shared/ports/scripted-confirmation-port'

const limits: SkillLimits = { instructionsMaxChars: 32000, descriptionMaxChars: 300, maxParameters: 20 }

const skill: Skill = {
  name: 'job-triage',
  displayName: 'Job triage',
  description: 'Triage a failed job',
  instructions: 'Investigate {{job_id}}',
  parameters: [{
    name: 'job_id',
    label: 'Job ID',
    help: '',
    required: true,
    default: null,
    inputType: 'text',
    options: [],
    placeholder: '12345',
    example: '',
  }],
  enabled: true,
  revision: 3,
  createdAt: null,
  updatedAt: null,
  updatedBy: null,
}
const other: Skill = { ...skill, name: 'perf-report', displayName: 'Performance report', enabled: false, revision: 1 }

const problem = (overrides: Partial<Problem>): Problem => ({ code: 'http-error', title: 'Request failed', ...overrides })

const gatewayWith = (overrides: Partial<SkillsGateway> = {}): SkillsGateway => ({
  list: jest.fn(async () => ok({ items: [toSummary(skill), toSummary(other)], limits })),
  get: jest.fn(async () => ok(skill)),
  create: jest.fn<SkillsGateway['create']>(async (request) => ok({ ...skill, ...request, revision: 1 })),
  patch: jest.fn<SkillsGateway['patch']>(async (_name, _revision, changes) => ok({ ...skill, ...changes, revision: 4 })),
  rollback: jest.fn(async () => ok<Skill>({ ...skill, revision: 4 })),
  setEnabled: jest.fn<SkillsGateway['setEnabled']>(async (_name, enabled) => ok({ ...skill, enabled })),
  remove: jest.fn(async () => ok(undefined)),
  listRevisions: jest.fn(async () => ok([{ revision: 3, updatedAt: null, updatedBy: null }])),
  ...overrides,
})

const createStore = (gateway: SkillsGateway, answers: readonly boolean[] = []) => {
  const confirmations = new ScriptedConfirmationPort(answers)
  const notifications = new RecordingNotificationPort()
  const store = new SkillsStore({
    gateway,
    confirmations,
    notifications,
    logger: { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} },
  })
  return { store, confirmations, notifications }
}

const openSkill = async (gateway = gatewayWith(), answers: readonly boolean[] = []) => {
  const created = createStore(gateway, answers)
  await created.store.loadList()
  await created.store.openEdit(skill.name)
  return created
}

describe('SkillsStore list', () => {
  it('loads the summaries and the server limits', async () => {
    const { store } = createStore(gatewayWith())

    await store.loadList()

    expect(store.getSnapshot()).toMatchObject({ listStatus: 'ready', limits })
    expect(store.getSnapshot().skills.map((row) => row.name)).toEqual(['job-triage', 'perf-report'])
  })

  it('keeps the list problem for the error state', async () => {
    const forbidden = problem({ status: 403 })
    const { store } = createStore(gatewayWith({ list: jest.fn(async () => err(forbidden)) }))

    await store.loadList()

    expect(store.getSnapshot()).toMatchObject({ listStatus: 'error', listProblem: forbidden })
  })
})

describe('SkillsStore enable/disable', () => {
  it('flips the switch before the server answers', async () => {
    let release: () => void = () => {}
    const setEnabled = jest.fn<SkillsGateway['setEnabled']>(() => new Promise((resolve) => {
      release = () => resolve(ok({ ...other, enabled: true, revision: 1 }))
    }))
    const { store } = createStore(gatewayWith({ setEnabled }))
    await store.loadList()

    const pending = store.setEnabled('perf-report', true)

    expect(store.getSnapshot().skills.find((row) => row.name === 'perf-report')?.enabled).toBe(true)
    expect(store.getSnapshot().toggling).toEqual(['perf-report'])
    release()
    await pending
    expect(store.getSnapshot().toggling).toEqual([])
  })

  it('rolls the switch back and reports when the server refuses', async () => {
    const { store, notifications } = createStore(gatewayWith({
      setEnabled: jest.fn(async () => err(problem({ status: 503, detail: 'store down' }))),
    }))
    await store.loadList()

    await store.setEnabled('perf-report', true)

    expect(store.getSnapshot().skills.find((row) => row.name === 'perf-report')?.enabled).toBe(false)
    expect(notifications.notifications).toEqual([
      { kind: 'error', message: SKILLS_COPY.list.enableFailure('perf-report'), options: { description: 'store down' } },
    ])
  })
})

describe('SkillsStore editing', () => {
  it('opens a skill with its revisions', async () => {
    const { store } = await openSkill()

    expect(store.getSnapshot()).toMatchObject({
      editor: { mode: 'edit', name: 'job-triage' },
      detail: skill,
      detailStatus: 'ready',
      revisionsStatus: 'ready',
    })
  })

  it('sends only the changed fields with the revision it started from', async () => {
    const gateway = gatewayWith()
    const { store, notifications } = await openSkill(gateway)

    await store.save({ ...contentOf(skill), description: 'New' })

    expect(gateway.patch).toHaveBeenCalledWith('job-triage', 3, { description: 'New' })
    expect(store.getSnapshot().detail?.revision).toBe(4)
    expect(store.getSnapshot().skills.find((row) => row.name === 'job-triage')?.revision).toBe(4)
    expect(notifications.messages()).toContain(SKILLS_COPY.editor.saveSuccess(4))
  })

  it('does not call the server when nothing changed', async () => {
    const gateway = gatewayWith()
    const { store, notifications } = await openSkill(gateway)

    expect(await store.save(contentOf(skill))).toBe(false)
    expect(gateway.patch).not.toHaveBeenCalled()
    expect(notifications.messages()).toEqual([SKILLS_COPY.editor.noChanges])
  })

  it('keeps a rejected save inline', async () => {
    const invalid = problem({ status: 400, serverCode: 'invalid_skill', detail: 'instructions too long' })
    const { store } = await openSkill(gatewayWith({ patch: jest.fn(async () => err({ kind: 'problem' as const, problem: invalid })) }))

    await store.save({ ...contentOf(skill), instructions: 'x' })

    expect(store.getSnapshot()).toMatchObject({ saving: false, saveProblem: invalid })
  })

  it('records a conflict and reapplies the admin changes on top of the stored revision', async () => {
    const theirs: Skill = { ...skill, displayName: 'Theirs', revision: 5 }
    const patch = jest.fn<SkillsGateway['patch']>()
      .mockResolvedValueOnce(err({ kind: 'conflict', current: theirs }))
      .mockResolvedValueOnce(ok({ ...theirs, description: 'Mine', revision: 6 }))
    const { store, notifications } = await openSkill(gatewayWith({ patch }))

    await store.save({ ...contentOf(skill), description: 'Mine' })

    expect(store.getSnapshot()).toMatchObject({ conflict: theirs, detail: skill })
    expect(notifications.messages()).toContain(SKILLS_COPY.conflict.toast)

    await store.save({ ...contentOf(skill), description: 'Mine' })

    expect(patch).toHaveBeenLastCalledWith('job-triage', 5, { description: 'Mine' })
    expect(store.getSnapshot()).toMatchObject({ conflict: null, detail: { revision: 6, displayName: 'Theirs' } })
  })

  it('adopts the stored version when the admin discards their changes', async () => {
    const theirs: Skill = { ...skill, displayName: 'Theirs', revision: 5 }
    const { store } = await openSkill(gatewayWith({ patch: jest.fn(async () => err({ kind: 'conflict' as const, current: theirs })) }))
    await store.save({ ...contentOf(skill), description: 'Mine' })

    store.discardConflict()

    expect(store.getSnapshot()).toMatchObject({ detail: theirs, conflict: null })
  })

  it('creates a skill and switches the editor to it', async () => {
    const gateway = gatewayWith()
    const { store } = createStore(gateway)
    await store.loadList()
    store.openCreate()

    await store.create({ ...contentOf(skill), name: 'a-new', enabled: false })

    expect(store.getSnapshot().editor).toEqual({ mode: 'edit', name: 'a-new' })
    expect(store.getSnapshot().skills.map((row) => row.name)).toEqual(['a-new', 'job-triage', 'perf-report'])
  })

  it('keeps a duplicate name inline on create', async () => {
    const exists = problem({ status: 409, serverCode: 'skill_exists' })
    const { store } = createStore(gatewayWith({ create: jest.fn(async () => err(exists)) }))
    store.openCreate()

    expect(await store.create({ ...contentOf(skill), name: 'job-triage', enabled: true })).toBe(false)
    expect(store.getSnapshot()).toMatchObject({ editor: { mode: 'create' }, saveProblem: exists })
  })

  it('stays open when the admin keeps unsaved changes', async () => {
    const { store, confirmations } = await openSkill(gatewayWith(), [false])

    expect(await store.closeEditor(true)).toBe(false)
    expect(confirmations.asked[0]?.title).toBe(SKILLS_COPY.editor.discardTitle)
    expect(store.getSnapshot().editor.mode).toBe('edit')
  })
})

describe('SkillsStore rollback and delete', () => {
  it('does nothing until the rollback is confirmed', async () => {
    const gateway = gatewayWith()
    const { store } = await openSkill(gateway, [false])

    expect(await store.rollback(1)).toBe(false)
    expect(gateway.rollback).not.toHaveBeenCalled()
  })

  it('rolls back against the current revision', async () => {
    const gateway = gatewayWith()
    const { store, notifications } = await openSkill(gateway, [true])

    expect(await store.rollback(1)).toBe(true)
    expect(gateway.rollback).toHaveBeenCalledWith('job-triage', 1, 3)
    expect(notifications.messages()).toContain(SKILLS_COPY.revisions.rollbackSuccess(4, 1))
  })

  it('deletes a confirmed skill and closes its editor', async () => {
    const gateway = gatewayWith()
    const { store } = await openSkill(gateway, [true])

    expect(await store.deleteSkill('job-triage')).toBe(true)
    expect(gateway.remove).toHaveBeenCalledWith('job-triage')
    expect(store.getSnapshot().editor.mode).toBe('closed')
    expect(store.getSnapshot().skills.map((row) => row.name)).toEqual(['perf-report'])
  })

  it('keeps the row when the delete fails', async () => {
    const { store, notifications } = createStore(gatewayWith({ remove: jest.fn(async () => err(problem({ status: 403 }))) }), [true])
    await store.loadList()

    expect(await store.deleteSkill('perf-report')).toBe(false)
    expect(store.getSnapshot().skills).toHaveLength(2)
    expect(notifications.messages()).toEqual([SKILLS_COPY.list.deleteFailure])
  })
})
