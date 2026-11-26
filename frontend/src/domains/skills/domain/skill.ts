import type { Instant } from '@/shared/kernel/instant'

export type SkillInputType = 'text' | 'number' | 'select' | 'checkbox'

export const SKILL_INPUT_TYPES: readonly SkillInputType[] = ['text', 'number', 'select', 'checkbox']

// Every field besides `name` and `required` is a presentation hint the server stores verbatim.
export type SkillParameter = {
  readonly name: string
  readonly label: string
  readonly help: string
  readonly required: boolean
  readonly default: string | null
  readonly inputType: SkillInputType
  readonly options: readonly string[]
  readonly placeholder: string
  readonly example: string
}

export type SkillContent = {
  readonly displayName: string
  readonly description: string
  readonly instructions: string
  readonly parameters: readonly SkillParameter[]
}

export type SkillSummary = {
  readonly name: string
  readonly displayName: string
  readonly description: string
  readonly parameters: readonly SkillParameter[]
  readonly enabled: boolean
  readonly revision: number
  readonly createdAt: Instant | null
  readonly updatedAt: Instant | null
  readonly updatedBy: string | null
}

export type Skill = SkillSummary & { readonly instructions: string }

export type SkillLimits = {
  readonly instructionsMaxChars: number
  readonly descriptionMaxChars: number
  readonly maxParameters: number
}

export type SkillRevisionSummary = {
  readonly revision: number
  readonly updatedAt: Instant | null
  readonly updatedBy: string | null
}

export type SkillCreateRequest = SkillContent & {
  readonly name: string
  readonly enabled: boolean
}

export type SkillContentPatch = Partial<SkillContent>

// Mirrors `autolangchat/skills/models.py`; the configurable limits arrive with the list instead.
export const SKILL_NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/
export const DISPLAY_NAME_MAX_CHARS = 100
export const PARAMETER_FIELD_MAX_CHARS = {
  label: 100,
  help: 500,
  default: 1000,
  placeholder: 200,
  example: 500,
} as const
export const MAX_PARAMETER_OPTIONS = 50
export const OPTION_MAX_CHARS = 200

// The server's limits use Python `len()` (code points), not UTF-16 units: an emoji counts once.
export const charCount = (value: string): number => Array.from(value).length

export const toSummary = (skill: Skill): SkillSummary => ({
  name: skill.name,
  displayName: skill.displayName,
  description: skill.description,
  parameters: skill.parameters,
  enabled: skill.enabled,
  revision: skill.revision,
  createdAt: skill.createdAt,
  updatedAt: skill.updatedAt,
  updatedBy: skill.updatedBy,
})

export const contentOf = (skill: Skill): SkillContent => ({
  displayName: skill.displayName,
  description: skill.description,
  instructions: skill.instructions,
  parameters: skill.parameters,
})

const sameParameters = (a: readonly SkillParameter[], b: readonly SkillParameter[]): boolean =>
  JSON.stringify(a) === JSON.stringify(b)

// The content fields that differ between `base` and `next`, so a PATCH only carries what the admin changed.
export const diffContent = (base: SkillContent, next: SkillContent): SkillContentPatch => ({
  ...(next.displayName === base.displayName ? {} : { displayName: next.displayName }),
  ...(next.description === base.description ? {} : { description: next.description }),
  ...(next.instructions === base.instructions ? {} : { instructions: next.instructions }),
  ...(sameParameters(base.parameters, next.parameters) ? {} : { parameters: next.parameters }),
})

export const filterSkills = (skills: readonly SkillSummary[], query: string): readonly SkillSummary[] => {
  const needle = query.trim().toLowerCase()
  if (needle === '') {
    return skills
  }
  return skills.filter((skill) =>
    [skill.name, skill.displayName, skill.description].some((text) => text.toLowerCase().includes(needle)),
  )
}
