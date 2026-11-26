import { HttpResponse, http } from 'msw'

import skillList from '../fixtures/skills/skill-list.json' with { type: 'json' }
import skillRevisions from '../fixtures/skills/skill-revisions.json' with { type: 'json' }
import skill from '../fixtures/skills/skill.json' with { type: 'json' }

const ADMIN = '*/chat/admin'

const written = () => HttpResponse.json({ ...skill, revision: skill.revision + 1 })

// Skill management (admin-api.md "Skill management").
export const skillsHandlers = [
  http.get(`${ADMIN}/skills`, () => HttpResponse.json(skillList)),
  http.post(`${ADMIN}/skills`, () => HttpResponse.json({ ...skill, revision: 1 }, { status: 201 })),
  http.get(`${ADMIN}/skills/:name`, () => HttpResponse.json(skill)),
  http.patch(`${ADMIN}/skills/:name`, written),
  http.post(`${ADMIN}/skills/:name/rollback`, written),
  http.post(`${ADMIN}/skills/:name/enable`, () => HttpResponse.json({ ...skill, enabled: true })),
  http.post(`${ADMIN}/skills/:name/disable`, () => HttpResponse.json({ ...skill, enabled: false })),
  http.get(`${ADMIN}/skills/:name/revisions`, () => HttpResponse.json(skillRevisions)),
  http.delete(`${ADMIN}/skills/:name`, () => new HttpResponse(null, { status: 204 })),
]

// `409 revision_conflict` as `admin_skill_routes._revision_conflict` writes it.
export const skillRevisionConflictHandler = http.patch(`${ADMIN}/skills/:name`, () =>
  HttpResponse.json(
    { code: 'revision_conflict', detail: "Skill 'job-triage' is at revision 4, not 3", current: { ...skill, revision: 4 } },
    { status: 409 },
  ),
)
