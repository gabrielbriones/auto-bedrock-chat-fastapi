import { afterAll, afterEach, beforeAll, describe, expect, it } from '@jest/globals'
import { HttpResponse, http } from 'msw'

import { HttpClient } from '@/shared/http/http-client'
import { isErr, isOk } from '@/shared/kernel/result'
import { contentOf } from '@/domains/skills/domain/public'
import { HttpSkillsGateway } from '@/domains/skills/infrastructure/http-skills.gateway'

import skillFixture from '../../../msw/fixtures/skills/skill.json' with { type: 'json' }
import { skillRevisionConflictHandler } from '../../../msw/handlers/skills'
import { server } from '../../../msw/server'

const ADMIN = 'http://localhost/chat/admin'
const silentLogger = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} }
const gateway = new HttpSkillsGateway(ADMIN, new HttpClient(), silentLogger)
const requests: { readonly method: string; readonly url: string; readonly body: unknown }[] = []

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
  server.events.on('request:start', async ({ request }) => {
    const text = await request.clone().text()
    requests.push({ method: request.method, url: request.url, body: text === '' ? undefined : JSON.parse(text) })
  })
})
afterEach(() => {
  server.resetHandlers()
  requests.length = 0
})
afterAll(() => server.close())

const signal = () => AbortSignal.timeout(1_000)

describe('HttpSkillsGateway', () => {
  it('reads a skill and returns every parameter hint unchanged on a write', async () => {
    const read = await gateway.get('job-triage', signal())
    if (!isOk(read)) throw new Error('expected the skill')

    await gateway.patch('job-triage', 3, { parameters: contentOf(read.value).parameters })

    expect(requests.at(-1)).toEqual({
      method: 'PATCH',
      url: `${ADMIN}/skills/job-triage`,
      body: { expected_revision: 3, parameters: skillFixture.parameters },
    })
  })

  it('sends a create in the wire shape', async () => {
    await gateway.create({ name: 'new-one', enabled: false, displayName: 'New', description: '', instructions: 'Do it', parameters: [] })

    expect(requests.at(-1)?.body).toEqual({
      name: 'new-one',
      enabled: false,
      display_name: 'New',
      description: '',
      instructions: 'Do it',
      parameters: [],
    })
  })

  it('rolls back with the expected revision', async () => {
    const result = await gateway.rollback('job-triage', 1, 3)

    expect(isOk(result) && result.value.revision).toBe(4)
    expect(requests.at(-1)).toMatchObject({ method: 'POST', url: `${ADMIN}/skills/job-triage/rollback`, body: { revision: 1, expected_revision: 3 } })
  })

  it('turns a 409 revision_conflict into a conflict carrying the stored skill', async () => {
    server.use(skillRevisionConflictHandler)

    const result = await gateway.patch('job-triage', 3, { description: 'Mine' })

    expect(isErr(result) && result.error.kind).toBe('conflict')
    expect(isErr(result) && result.error.kind === 'conflict' && result.error.current.name).toBe('job-triage')
  })

  it('reports a rejected skill as a problem with the server code', async () => {
    server.use(http.patch(`${ADMIN}/skills/:name`, () =>
      HttpResponse.json({ code: 'invalid_skill', detail: 'instructions too long' }, { status: 400 })))

    const result = await gateway.patch('job-triage', 3, { instructions: 'x' })

    expect(isErr(result) && result.error.kind === 'problem' && result.error.problem).toMatchObject({
      status: 400,
      serverCode: 'invalid_skill',
      detail: 'instructions too long',
    })
  })

  it('toggles, lists revisions and deletes on the documented paths', async () => {
    expect(isOk(await gateway.setEnabled('job-triage', false))).toBe(true)
    expect(requests.at(-1)).toMatchObject({ method: 'POST', url: `${ADMIN}/skills/job-triage/disable` })

    const revisions = await gateway.listRevisions('job-triage', signal())
    expect(isOk(revisions) && revisions.value.map((entry) => entry.revision)).toEqual([3, 2, 1])

    expect(isOk(await gateway.remove('job-triage'))).toBe(true)
    expect(requests.at(-1)).toMatchObject({ method: 'DELETE', url: `${ADMIN}/skills/job-triage` })
  })
})
