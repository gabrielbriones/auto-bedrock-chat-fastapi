import type { Problem } from '@/shared/http/exception'
import { SKILLS_COPY } from '@/shared/copy/skills'

import type { SkillIssue } from '@/domains/skills/domain/public'

export const issueMessage = (issue: SkillIssue | undefined): string | null => {
  if (issue === undefined) return null
  switch (issue.kind) {
    case 'required':
      return SKILLS_COPY.issues.required
    case 'pattern':
      return SKILLS_COPY.issues.pattern
    case 'duplicate':
      return SKILLS_COPY.issues.duplicate
    case 'too-long':
      return SKILLS_COPY.issues.tooLong(issue.max)
    case 'too-many':
      return SKILLS_COPY.issues.tooMany(issue.max)
    case 'unknown-placeholders':
      return SKILLS_COPY.issues.unknownPlaceholders(issue.names.map((name) => `{{${name}}}`).join(', '))
  }
}

// The admin API answers `400 invalid_skill` (or FastAPI's `422`) for content the server rejects,
// `409 skill_exists` for a taken name, `403` once admin access is gone and `503` without a store.
export const problemMessage = (problem: Problem, name?: string): string => {
  const detail = problem.detail ?? problem.title
  switch (problem.status) {
    case 400:
    case 422:
      return SKILLS_COPY.problems.invalid(detail)
    case 401:
    case 403:
      return SKILLS_COPY.problems.forbidden
    case 404:
      return SKILLS_COPY.problems.notFound
    case 409:
      return name === undefined ? detail : SKILLS_COPY.problems.exists(name)
    case 503:
      return SKILLS_COPY.problems.unavailable
    default:
      return SKILLS_COPY.problems.generic
  }
}
