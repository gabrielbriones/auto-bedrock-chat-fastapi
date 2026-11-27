import { afterAll, afterEach, beforeAll, describe, expect, it } from '@jest/globals'

import { isOk } from '@/shared/kernel/result'
import { toSkill, toSkillList, toSkillRevisions } from '@/domains/skills/infrastructure/dto/skill.dto'
import { toSkillCatalog } from '@/domains/skills/infrastructure/dto/skill-catalog.dto'

import { server } from './server'

// CT-2, for the skills context: every fixture the handlers answer with is parsed by the same
// production mapper the dashboard uses, so a fixture drifting from admin-api.md fails here first.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ADMIN = 'http://localhost/chat/admin'

const fetchJson = async (path: string, init?: RequestInit, status = 200): Promise<unknown> => {
  const response = await fetch(`${ADMIN}${path}`, init)

  expect(response.status).toBe(status)
  return response.json()
}

describe('skills REST contract', () => {
  it('GET {chat}/skills/catalog parses into catalog entries', async () => {
    const response = await fetch('http://localhost/chat/skills/catalog')
    const result = toSkillCatalog(await response.json())

    expect(result).toMatchObject({
      kind: 'ok',
      value: [{ name: 'job-triage', displayName: 'Job triage', revision: 3 }],
    })
  })

  it('GET /skills parses into summaries and limits', async () => {
    const result = toSkillList(await fetchJson('/skills'))

    expect(isOk(result) && result.value.limits).toEqual({ instructionsMaxChars: 32000, descriptionMaxChars: 300, maxParameters: 20 })
  })

  it('GET /skills/{name} parses into a skill with instructions', async () => {
    expect(isOk(toSkill(await fetchJson('/skills/job-triage')))).toBe(true)
  })

  it('GET /skills/{name}/revisions parses newest first', async () => {
    const result = toSkillRevisions(await fetchJson('/skills/job-triage/revisions'))

    expect(isOk(result) && result.value.map((entry) => entry.revision)).toEqual([3, 2, 1])
  })

  it.each([
    ['POST', '/skills', 201],
    ['PATCH', '/skills/job-triage', 200],
    ['POST', '/skills/job-triage/rollback', 200],
    ['POST', '/skills/job-triage/enable', 200],
    ['POST', '/skills/job-triage/disable', 200],
  ])('%s %s answers %i with a skill', async (method, path, status) => {
    expect(isOk(toSkill(await fetchJson(path, { method, body: '{}' }, status)))).toBe(true)
  })

  it('DELETE /skills/{name} answers 204 without a body', async () => {
    const response = await fetch(`${ADMIN}/skills/job-triage`, { method: 'DELETE' })

    expect(response.status).toBe(204)
  })
})
