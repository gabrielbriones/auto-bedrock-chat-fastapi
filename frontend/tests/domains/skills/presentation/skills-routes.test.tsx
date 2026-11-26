import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { describe, expect, it, jest } from '@jest/globals'
import { axe } from 'jest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import type { Container } from '@/app/bootstrap/container'
import { ConfirmationHost } from '@/app/providers/ConfirmationHost'
import { createAppRouter } from '@/app/router'
import type { SkillsGateway } from '@/domains/skills/application/ports'
import { SkillsStore } from '@/domains/skills/application/skills.store'
import { toSummary, type Skill, type SkillLimits } from '@/domains/skills/domain/public'
import { ADMIN_COPY } from '@/shared/copy/admin'
import { SHELL } from '@/shared/copy/shell'
import { SKILLS_COPY } from '@/shared/copy/skills'
import type { Problem } from '@/shared/http/exception'
import { err, ok } from '@/shared/kernel/result'

import { fakeContainer } from '../../../app/bootstrap/container.fixture'
import { RecordingNotificationPort } from '../../../shared/ports/recording-notification-port'

const LIST = SKILLS_COPY.list
const EDITOR = SKILLS_COPY.editor
const PARAMS = SKILLS_COPY.parameters
const PATH = '/chat/dashboard/skills'

const limits: SkillLimits = { instructionsMaxChars: 32000, descriptionMaxChars: 300, maxParameters: 20 }

const triage: Skill = {
  name: 'job-triage',
  displayName: 'Job triage',
  description: 'Triage a failed job',
  instructions: 'Investigate {{job_id}} at {{depth}} depth. <b>not markup</b>',
  parameters: [
    { name: 'job_id', label: 'Job ID', help: 'The id', required: true, default: null, inputType: 'text', options: [], placeholder: '12345', example: '98765' },
    { name: 'depth', label: 'Depth', help: '', required: false, default: 'summary', inputType: 'select', options: ['summary', 'full'], placeholder: '', example: '' },
  ],
  enabled: true,
  revision: 3,
  createdAt: null,
  updatedAt: null,
  updatedBy: 'admin@example.com',
}
const report: Skill = { ...triage, name: 'perf-report', displayName: 'Performance report', description: 'Summarise a run', parameters: [], enabled: false, revision: 1 }

const createGateway = (overrides: Partial<SkillsGateway> = {}): SkillsGateway => ({
  list: jest.fn(async () => ok({ items: [toSummary(triage), toSummary(report)], limits })),
  get: jest.fn(async (name) => ok(name === report.name ? report : triage)),
  create: jest.fn<SkillsGateway['create']>(async (request) => ok({ ...triage, ...request, revision: 1 })),
  patch: jest.fn<SkillsGateway['patch']>(async (_name, _revision, changes) => ok({ ...triage, ...changes, revision: 4 })),
  rollback: jest.fn(async () => ok<Skill>({ ...triage, revision: 4 })),
  setEnabled: jest.fn<SkillsGateway['setEnabled']>(async (name, enabled) => ok({ ...(name === report.name ? report : triage), enabled })),
  remove: jest.fn(async () => ok(undefined)),
  listRevisions: jest.fn(async () => ok([3, 2, 1].map((revision) => ({ revision, updatedAt: null, updatedBy: null })))),
  ...overrides,
})

const renderAt = (gateway = createGateway(), path = PATH) => {
  const base = fakeContainer()
  const notifications = new RecordingNotificationPort()
  const skills = new SkillsStore({ gateway, confirmations: base.confirmations, notifications, logger: base.logger })
  const container: Container = { ...base, skills }
  const router = createAppRouter(container, { history: createMemoryHistory({ initialEntries: [path] }) })
  const view = render(
    <ContainerContext.Provider value={container}>
      <RouterProvider router={router} />
      <ConfirmationHost controller={container.confirmations} />
    </ContainerContext.Provider>,
  )
  return { gateway, notifications, dom: view.container, router, skills }
}

const rowOf = async (name: string) => (await screen.findByText(name, { selector: 'td *, td' })).closest('tr') as HTMLElement

const openEditor = async (user: ReturnType<typeof userEvent.setup>, name = triage.name) => {
  await user.click(await screen.findByRole('button', { name: ADMIN_COPY.table.open(LIST.open(name)) }))
  return screen.findByRole('form', { name: EDITOR.editTitle(name) })
}

const confirmWith = async (user: ReturnType<typeof userEvent.setup>, label: string) => {
  await user.click(await screen.findByRole('button', { name: label }))
}

describe('skills list', () => {
  it('lists skills with their state and revision', async () => {
    renderAt()

    expect(await screen.findByRole('heading', { level: 1, name: SHELL.admin.skills })).toBeInTheDocument()
    const row = await rowOf('perf-report')
    expect(within(row).getByText('Performance report')).toBeInTheDocument()
    expect(within(row).getByRole('switch', { name: LIST.toggleLabel('perf-report') })).not.toBeChecked()
    expect(within(row).getByText('1')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: SHELL.admin.skills })).toBeInTheDocument()
  })

  it('filters by the search in the URL', async () => {
    renderAt(createGateway(), `${PATH}?q=summarise`)

    await rowOf('perf-report')
    expect(screen.queryByText('job-triage')).not.toBeInTheDocument()
  })

  it('flips a switch at once and flips it back when the server refuses', async () => {
    const user = userEvent.setup()
    const setEnabled = jest.fn<SkillsGateway['setEnabled']>(async () => err<Problem>({ code: 'http-error', title: 'Forbidden', status: 403 }))
    const { notifications } = renderAt(createGateway({ setEnabled }))

    const toggle = within(await rowOf('perf-report')).getByRole('switch', { name: LIST.toggleLabel('perf-report') })
    await user.click(toggle)

    expect(setEnabled).toHaveBeenCalledWith('perf-report', true)
    await waitFor(() => expect(toggle).not.toBeChecked())
    expect(notifications.messages()).toContain(LIST.enableFailure('perf-report'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('deletes a skill only after confirmation', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    await user.click(within(await rowOf('perf-report')).getByRole('button', { name: LIST.deleteLabel('perf-report') }))
    await confirmWith(user, LIST.deleteConfirmLabel)

    await waitFor(() => expect(screen.queryByText('perf-report')).not.toBeInTheDocument())
    expect(gateway.remove).toHaveBeenCalledWith('perf-report')
  })

  it('has no accessibility violations', async () => {
    const { dom } = renderAt()

    await rowOf('job-triage')
    const results = await axe(dom)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})

describe('skill editor', () => {
  it('blocks an incomplete new skill inline and never reaches the server', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    await user.click(await screen.findByRole('button', { name: LIST.create }))
    const form = await screen.findByRole('form', { name: EDITOR.createTitle })
    await user.click(within(form).getByRole('button', { name: EDITOR.create }))

    expect(within(form).getAllByText(SKILLS_COPY.issues.required)).toHaveLength(3)
    expect(within(form).getByRole('textbox', { name: EDITOR.displayName })).toHaveAttribute('aria-invalid', 'true')
    expect(gateway.create).not.toHaveBeenCalled()
  })

  it('shows a rejected create inline', async () => {
    const user = userEvent.setup()
    const create = jest.fn<SkillsGateway['create']>(async () =>
      err<Problem>({ code: 'http-error', title: 'Bad Request', status: 400, serverCode: 'invalid_skill', detail: 'too many parameters' }))
    renderAt(createGateway({ create }))

    await user.click(await screen.findByRole('button', { name: LIST.create }))
    const form = await screen.findByRole('form', { name: EDITOR.createTitle })
    await user.type(within(form).getByRole('textbox', { name: EDITOR.name }), 'new-skill')
    await user.type(within(form).getByRole('textbox', { name: EDITOR.displayName }), 'New skill')
    await user.type(within(form).getByRole('textbox', { name: EDITOR.instructions }), 'Do it')
    await user.click(within(form).getByRole('button', { name: EDITOR.create }))

    expect(await within(form).findByText(SKILLS_COPY.problems.invalid('too many parameters'))).toBeInTheDocument()
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ name: 'new-skill', enabled: true, parameters: [] }))
  })

  it('adds a parameter for a placeholder typed into the instructions and explains why', async () => {
    const user = userEvent.setup()
    renderAt()

    await user.click(await screen.findByRole('button', { name: LIST.create }))
    const form = await screen.findByRole('form', { name: EDITOR.createTitle })
    await user.type(within(form).getByRole('textbox', { name: EDITOR.instructions }), 'Print {{{{argument1}}')

    const row = within(form).getByRole('group', { name: PARAMS.row(1) })
    expect(within(row).getByRole('textbox', { name: PARAMS.name })).toHaveValue('argument1')
    expect(within(row).getByText(PARAMS.autoAdded)).toBeInTheDocument()
    expect(within(form).queryByRole('group', { name: PARAMS.row(2) })).not.toBeInTheDocument()
  })

  it('keeps an auto-added parameter the admin renamed when the instructions change again', async () => {
    const user = userEvent.setup()
    renderAt()

    await user.click(await screen.findByRole('button', { name: LIST.create }))
    const form = await screen.findByRole('form', { name: EDITOR.createTitle })
    const instructions = within(form).getByRole('textbox', { name: EDITOR.instructions })
    await user.type(instructions, 'Use {{{{foo}}')
    const name = within(within(form).getByRole('group', { name: PARAMS.row(1) })).getByRole('textbox', { name: PARAMS.name })
    await user.clear(name)
    await user.type(name, 'bar')
    await user.type(instructions, ' now')

    const row = within(form).getByRole('group', { name: PARAMS.row(1) })
    expect(within(row).getByRole('textbox', { name: PARAMS.name })).toHaveValue('bar')
    expect(within(row).queryByText(PARAMS.autoAdded)).not.toBeInTheDocument()
  })

  it('flags a placeholder whose parameter was removed, offers to add it back, and blocks saving', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    const form = await openEditor(user)
    await user.click(within(form).getByRole('button', { name: PARAMS.remove(1) }))

    expect(within(form).getByText(SKILLS_COPY.preview.missingTitle)).toBeInTheDocument()
    await user.click(within(form).getByRole('button', { name: EDITOR.save }))
    expect(gateway.patch).not.toHaveBeenCalled()

    await user.click(within(form).getByRole('button', { name: SKILLS_COPY.preview.addMissing('job_id') }))
    expect(within(form).queryByText(SKILLS_COPY.preview.missingTitle)).not.toBeInTheDocument()
    expect(within(within(form).getByRole('group', { name: PARAMS.row(2) })).getByRole('textbox', { name: PARAMS.name })).toHaveValue('job_id')
  })

  it('previews the instructions with sample values as plain text', async () => {
    const user = userEvent.setup()
    renderAt()

    await openEditor(user)
    const preview = screen.getByTestId('skill-preview')

    expect(preview).toHaveTextContent('Investigate 98765 at summary depth. <b>not markup</b>')
    expect(preview.querySelector('b')).toBeNull()
  })

  it('reorders parameters and sends their hints back unchanged', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    const form = await openEditor(user)
    await user.click(within(form).getByRole('button', { name: PARAMS.moveDown(1) }))
    expect(within(within(form).getByRole('group', { name: PARAMS.row(1) })).getByRole('textbox', { name: PARAMS.name })).toHaveValue('depth')
    await user.click(within(form).getByRole('button', { name: EDITOR.save }))

    await waitFor(() => expect(gateway.patch).toHaveBeenCalledWith('job-triage', 3, {
      parameters: [...triage.parameters].reverse(),
    }))
  })

  it('shows the stored version on a conflict and reapplies on top of it', async () => {
    const user = userEvent.setup()
    const theirs: Skill = { ...triage, displayName: 'Their triage', revision: 5 }
    const patch = jest.fn<SkillsGateway['patch']>()
      .mockResolvedValueOnce(err({ kind: 'conflict', current: theirs }))
      .mockResolvedValueOnce(ok({ ...theirs, description: 'Mine', revision: 6 }))
    renderAt(createGateway({ patch }))

    const form = await openEditor(user)
    const description = within(form).getByRole('textbox', { name: EDITOR.description })
    await user.clear(description)
    await user.type(description, 'Mine')
    await user.click(within(form).getByRole('button', { name: EDITOR.save }))

    expect(await within(form).findByText(SKILLS_COPY.conflict.title(5))).toBeInTheDocument()
    expect(within(form).getByText('Their triage')).toBeInTheDocument()
    expect(description).toHaveValue('Mine')
    await user.click(within(form).getByRole('button', { name: SKILLS_COPY.conflict.reapply(5) }))

    await waitFor(() => expect(patch).toHaveBeenLastCalledWith('job-triage', 5, { description: 'Mine' }))
    expect(await screen.findByRole('textbox', { name: EDITOR.displayName })).toHaveValue('Their triage')
    expect(screen.queryByText(SKILLS_COPY.conflict.title(5))).not.toBeInTheDocument()
  })

  it('lets the admin drop their changes after a conflict', async () => {
    const user = userEvent.setup()
    const theirs: Skill = { ...triage, displayName: 'Their triage', revision: 5 }
    renderAt(createGateway({ patch: jest.fn(async () => err({ kind: 'conflict' as const, current: theirs })) }))

    const form = await openEditor(user)
    await user.type(within(form).getByRole('textbox', { name: EDITOR.description }), '!')
    await user.click(within(form).getByRole('button', { name: EDITOR.save }))
    await user.click(await within(form).findByRole('button', { name: SKILLS_COPY.conflict.discard }))

    expect(await screen.findByRole('textbox', { name: EDITOR.displayName })).toHaveValue('Their triage')
    expect(screen.getByRole('textbox', { name: EDITOR.description })).toHaveValue(triage.description)
  })

  it('rolls back to an older revision after confirmation', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    await openEditor(user)
    await user.click(await screen.findByRole('button', { name: SKILLS_COPY.revisions.rollbackLabel(2) }))
    await confirmWith(user, SKILLS_COPY.revisions.rollbackConfirmLabel)

    await waitFor(() => expect(gateway.rollback).toHaveBeenCalledWith('job-triage', 2, 3))
  })

  it('deletes the open skill from the editor and closes it', async () => {
    const user = userEvent.setup()
    const { gateway } = renderAt()

    await openEditor(user)
    await user.click(screen.getByRole('button', { name: EDITOR.delete }))
    await confirmWith(user, LIST.deleteConfirmLabel)

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(gateway.remove).toHaveBeenCalledWith('job-triage')
  })

  it('asks before leaving the page with unsaved edits and closes the editor once the admin leaves', async () => {
    const user = userEvent.setup()
    const { router, skills } = renderAt()

    const form = await openEditor(user)
    const displayName = within(form).getByRole('textbox', { name: EDITOR.displayName })
    await user.type(displayName, ' edited')

    void router.navigate({ to: '/dashboard/kb-browser' })
    await confirmWith(user, SHELL.confirmations.cancel)
    expect(router.state.location.pathname).toBe('/dashboard/skills')
    expect(displayName).toHaveValue('Job triage edited')

    void router.navigate({ to: '/dashboard/kb-browser' })
    await confirmWith(user, EDITOR.discardConfirmLabel)
    await waitFor(() => expect(router.state.location.pathname).toBe('/dashboard/kb-browser'))
    expect(skills.getSnapshot().editor.mode).toBe('closed')
  })

  it('has no accessibility violations', async () => {
    const user = userEvent.setup()
    renderAt()

    await openEditor(user)
    const results = await axe(screen.getByRole('dialog'))

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
