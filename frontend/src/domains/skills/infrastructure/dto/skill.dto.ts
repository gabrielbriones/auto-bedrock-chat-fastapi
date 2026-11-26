import { z } from 'zod'

import { invalidResponseProblem, type Problem } from '@/shared/http/exception'
import { issuesOf, nullableInstantSchema, nullableTextSchema } from '@/shared/http/wire'
import { err, ok, type Result } from '@/shared/kernel/result'

import type { SkillList } from '@/domains/skills/application/ports'
import type {
  Skill,
  SkillContentPatch,
  SkillCreateRequest,
  SkillParameter,
  SkillRevisionSummary,
  SkillSummary,
} from '@/domains/skills/domain/public'

export const skillParameterDtoSchema = z.object({
  name: z.string().min(1),
  label: z.string().default(''),
  help: z.string().default(''),
  required: z.boolean().default(false),
  default: nullableTextSchema,
  input_type: z.enum(['text', 'number', 'select', 'checkbox']).default('text'),
  options: z.array(z.string()).default([]),
  placeholder: z.string().default(''),
  example: z.string().default(''),
})

export const skillSummaryDtoSchema = z.object({
  name: z.string().min(1),
  display_name: z.string(),
  description: z.string().default(''),
  parameters: z.array(skillParameterDtoSchema).default([]),
  enabled: z.boolean(),
  revision: z.number().int().min(1).default(1),
  created_at: nullableInstantSchema,
  updated_at: nullableInstantSchema,
  updated_by: nullableTextSchema,
})

export const skillDtoSchema = skillSummaryDtoSchema.extend({ instructions: z.string() })

export const skillListDtoSchema = z.object({
  items: z.array(skillSummaryDtoSchema),
  limits: z.object({
    skill_instructions_max_chars: z.number().int().min(1),
    skill_description_max_chars: z.number().int().min(1),
    skill_max_parameters: z.number().int().min(0),
  }),
})

export const skillRevisionListDtoSchema = z.object({
  items: z.array(z.object({
    revision: z.number().int().min(1),
    updated_at: nullableInstantSchema,
    updated_by: nullableTextSchema,
  })),
})

type ParameterDto = z.output<typeof skillParameterDtoSchema>
type SummaryDto = z.output<typeof skillSummaryDtoSchema>

const toParameter = (dto: ParameterDto): SkillParameter => ({
  name: dto.name,
  label: dto.label,
  help: dto.help,
  required: dto.required,
  default: dto.default,
  inputType: dto.input_type,
  options: dto.options,
  placeholder: dto.placeholder,
  example: dto.example,
})

const toSummary = (dto: SummaryDto): SkillSummary => ({
  name: dto.name,
  displayName: dto.display_name,
  description: dto.description,
  parameters: dto.parameters.map(toParameter),
  enabled: dto.enabled,
  revision: dto.revision,
  createdAt: dto.created_at,
  updatedAt: dto.updated_at,
  updatedBy: dto.updated_by,
})

const parseWith = <S extends z.ZodType, T>(schema: S, title: string, map: (dto: z.output<S>) => T) =>
  (value: unknown): Result<T, Problem> => {
    const parsed = schema.safeParse(value)
    return parsed.success ? ok(map(parsed.data)) : err(invalidResponseProblem(title, issuesOf(parsed.error)))
  }

export const toSkill = parseWith(skillDtoSchema, 'Invalid skill', (dto): Skill => ({
  ...toSummary(dto),
  instructions: dto.instructions,
}))

export const toSkillList = parseWith(skillListDtoSchema, 'Invalid skill list', (dto): SkillList => ({
  items: dto.items.map(toSummary),
  limits: {
    instructionsMaxChars: dto.limits.skill_instructions_max_chars,
    descriptionMaxChars: dto.limits.skill_description_max_chars,
    maxParameters: dto.limits.skill_max_parameters,
  },
}))

export const toSkillRevisions = parseWith(
  skillRevisionListDtoSchema,
  'Invalid skill revision list',
  (dto): readonly SkillRevisionSummary[] => dto.items.map((item) => ({
    revision: item.revision,
    updatedAt: item.updated_at,
    updatedBy: item.updated_by,
  })),
)

// Every hint is sent back as stored, so a round trip through the editor never rewrites one.
export const fromParameter = (parameter: SkillParameter) => ({
  name: parameter.name,
  label: parameter.label,
  help: parameter.help,
  required: parameter.required,
  default: parameter.default,
  input_type: parameter.inputType,
  options: parameter.options,
  placeholder: parameter.placeholder,
  example: parameter.example,
})

export const fromCreateRequest = (request: SkillCreateRequest) => ({
  name: request.name,
  enabled: request.enabled,
  display_name: request.displayName,
  description: request.description,
  instructions: request.instructions,
  parameters: request.parameters.map(fromParameter),
})

export const fromContentPatch = (expectedRevision: number, changes: SkillContentPatch) => ({
  expected_revision: expectedRevision,
  ...(changes.displayName === undefined ? {} : { display_name: changes.displayName }),
  ...(changes.description === undefined ? {} : { description: changes.description }),
  ...(changes.instructions === undefined ? {} : { instructions: changes.instructions }),
  ...(changes.parameters === undefined ? {} : { parameters: changes.parameters.map(fromParameter) }),
})
