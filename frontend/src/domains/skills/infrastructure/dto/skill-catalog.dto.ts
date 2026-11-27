import { z } from 'zod'

import { invalidResponseProblem, type Problem } from '@/shared/http/exception'
import { issuesOf } from '@/shared/http/wire'
import { err, ok, type Result } from '@/shared/kernel/result'

import type { SkillCatalogEntry } from '@/domains/skills/domain/public'
import { skillParameterDtoSchema, toParameter } from '@/domains/skills/infrastructure/dto/skill.dto'

export const skillCatalogDtoSchema = z.object({
  items: z.array(
    z.object({
      name: z.string().min(1),
      display_name: z.string(),
      description: z.string().default(''),
      parameters: z.array(skillParameterDtoSchema).default([]),
      revision: z.number().int().min(1).default(1),
    }),
  ),
})

export const toSkillCatalog = (value: unknown): Result<readonly SkillCatalogEntry[], Problem> => {
  const parsed = skillCatalogDtoSchema.safeParse(value)
  if (!parsed.success) {
    return err(invalidResponseProblem('Invalid skill catalog', issuesOf(parsed.error)))
  }
  return ok(
    parsed.data.items.map((item) => ({
      name: item.name,
      displayName: item.display_name,
      description: item.description,
      parameters: item.parameters.map(toParameter),
      revision: item.revision,
    })),
  )
}
