import {
  charCount,
  DISPLAY_NAME_MAX_CHARS,
  MAX_PARAMETER_OPTIONS,
  OPTION_MAX_CHARS,
  PARAMETER_FIELD_MAX_CHARS,
  SKILL_NAME_PATTERN,
  type Skill,
  type SkillContent,
  type SkillLimits,
  type SkillParameter,
} from '@/domains/skills/domain/skill'
import { placeholderNames } from '@/domains/skills/domain/skill-preview'

// `key` only identifies a row while it is edited (reordering must not remount its inputs).
// `autoAdded` marks a row the editor created for a `{{name}}` typed into the instructions.
export type SkillParameterDraft = SkillParameter & { readonly key: string; readonly autoAdded: boolean }

export type SkillDraft = {
  readonly name: string
  readonly displayName: string
  readonly description: string
  readonly instructions: string
  readonly parameters: readonly SkillParameterDraft[]
}

export type SkillField = 'name' | 'displayName' | 'description' | 'instructions' | 'parameters'
export type ParameterField = 'name' | 'options' | keyof typeof PARAMETER_FIELD_MAX_CHARS

export type SkillIssue =
  | { readonly kind: 'required' }
  | { readonly kind: 'pattern' }
  | { readonly kind: 'duplicate' }
  | { readonly kind: 'too-long'; readonly max: number }
  | { readonly kind: 'too-many'; readonly max: number }
  | { readonly kind: 'unknown-placeholders'; readonly names: readonly string[] }

export type ParameterIssues = Partial<Record<ParameterField, SkillIssue>>

export type SkillDraftIssues = {
  readonly fields: Partial<Record<SkillField, SkillIssue>>
  readonly parameters: readonly ParameterIssues[]
}

export const newParameterDraft = (key: string, name = '', autoAdded = false): SkillParameterDraft => ({
  key,
  autoAdded,
  name,
  label: '',
  help: '',
  required: false,
  default: null,
  inputType: 'text',
  options: [],
  placeholder: '',
  example: '',
})

export const EMPTY_SKILL_DRAFT: SkillDraft = {
  name: '',
  displayName: '',
  description: '',
  instructions: '',
  parameters: [],
}

export const draftFromSkill = (skill: Skill): SkillDraft => ({
  name: skill.name,
  displayName: skill.displayName,
  description: skill.description,
  instructions: skill.instructions,
  parameters: skill.parameters.map((parameter, index) => ({ ...parameter, key: `stored-${index}`, autoAdded: false })),
})

const toParameter = (parameter: SkillParameterDraft): SkillParameter => ({
  name: parameter.name,
  label: parameter.label,
  help: parameter.help,
  required: parameter.required,
  default: parameter.default,
  inputType: parameter.inputType,
  // Blank lines are what an admin leaves while typing a list; they are never options.
  options: parameter.options.filter((option) => option !== ''),
  placeholder: parameter.placeholder,
  example: parameter.example,
})

export const contentFromDraft = (draft: SkillDraft): SkillContent => ({
  displayName: draft.displayName,
  description: draft.description,
  instructions: draft.instructions,
  parameters: draft.parameters.map(toParameter),
})

export const moveParameter = (
  parameters: readonly SkillParameterDraft[],
  index: number,
  offset: -1 | 1,
): readonly SkillParameterDraft[] => {
  const target = index + offset
  const moving = parameters[index]
  const other = parameters[target]
  if (moving === undefined || other === undefined) {
    return parameters
  }
  const next = [...parameters]
  next[index] = other
  next[target] = moving
  return next
}

const tooLong = (value: string, max: number): SkillIssue | undefined =>
  charCount(value) > max ? { kind: 'too-long', max } : undefined

const isUntouched = (parameter: SkillParameterDraft): boolean =>
  JSON.stringify(toParameter(parameter)) === JSON.stringify(toParameter(newParameterDraft('', parameter.name)))

// `{{name}}` placeholders that no parameter row declares.
export const unknownPlaceholders = (instructions: string, parameters: readonly SkillParameter[]): readonly string[] => {
  const declared = new Set(parameters.map((parameter) => parameter.name))
  return placeholderNames(instructions).filter((name) => !declared.has(name))
}

// Adds a row for each new valid `{{name}}` and drops auto-added rows the admin never edited once their
// placeholder is gone, so typing a name inside `{{}}` doesn't leave a row per keystroke behind.
export const syncParametersWithInstructions = (
  parameters: readonly SkillParameterDraft[],
  instructions: string,
  nextKey: () => string,
): readonly SkillParameterDraft[] => {
  const referenced = new Set(placeholderNames(instructions))
  const kept = parameters.filter((parameter) => !(parameter.autoAdded && !referenced.has(parameter.name) && isUntouched(parameter)))
  const added = unknownPlaceholders(instructions, kept)
    .filter((name) => SKILL_NAME_PATTERN.test(name))
    .map((name) => newParameterDraft(nextKey(), name, true))
  return kept.length === parameters.length && added.length === 0 ? parameters : [...kept, ...added]
}

const instructionsIssue = (draft: SkillDraft, limits: SkillLimits | null): SkillIssue | undefined => {
  if (draft.instructions.trim() === '') return { kind: 'required' }
  const long = limits === null ? undefined : tooLong(draft.instructions, limits.instructionsMaxChars)
  if (long !== undefined) return long
  const unknown = unknownPlaceholders(draft.instructions, draft.parameters)
  return unknown.length === 0 ? undefined : { kind: 'unknown-placeholders', names: unknown }
}

const nameIssue = (name: string): SkillIssue | undefined => {
  if (name === '') return { kind: 'required' }
  return SKILL_NAME_PATTERN.test(name) ? undefined : { kind: 'pattern' }
}

const optionsIssue = (options: readonly string[]): SkillIssue | undefined => {
  const kept = options.filter((option) => option !== '')
  if (kept.length > MAX_PARAMETER_OPTIONS) return { kind: 'too-many', max: MAX_PARAMETER_OPTIONS }
  return kept.some((option) => charCount(option) > OPTION_MAX_CHARS) ? { kind: 'too-long', max: OPTION_MAX_CHARS } : undefined
}

const compact = <K extends string>(entries: Record<K, SkillIssue | undefined>): Partial<Record<K, SkillIssue>> =>
  Object.fromEntries(Object.entries(entries).filter(([, issue]) => issue !== undefined)) as Partial<Record<K, SkillIssue>>

const parameterIssues = (parameter: SkillParameterDraft, duplicate: boolean): ParameterIssues => compact({
  name: duplicate ? { kind: 'duplicate' } : nameIssue(parameter.name),
  label: tooLong(parameter.label, PARAMETER_FIELD_MAX_CHARS.label),
  help: tooLong(parameter.help, PARAMETER_FIELD_MAX_CHARS.help),
  default: tooLong(parameter.default ?? '', PARAMETER_FIELD_MAX_CHARS.default),
  placeholder: tooLong(parameter.placeholder, PARAMETER_FIELD_MAX_CHARS.placeholder),
  example: tooLong(parameter.example, PARAMETER_FIELD_MAX_CHARS.example),
  options: optionsIssue(parameter.options),
})

// The client-side mirror of the server's checks, so most mistakes never cost a round trip.
// `limits` is `null` until the list has loaded; the server still enforces them.
export const validateSkillDraft = (draft: SkillDraft, limits: SkillLimits | null, isNew: boolean): SkillDraftIssues => {
  const names = draft.parameters.map((parameter) => parameter.name)
  const isDuplicate = (name: string) => name !== '' && names.indexOf(name) !== names.lastIndexOf(name)

  return {
    fields: compact({
      name: isNew ? nameIssue(draft.name) : undefined,
      displayName: draft.displayName.trim() === '' ? { kind: 'required' } : tooLong(draft.displayName, DISPLAY_NAME_MAX_CHARS),
      description: limits === null ? undefined : tooLong(draft.description, limits.descriptionMaxChars),
      instructions: instructionsIssue(draft, limits),
      parameters: limits !== null && draft.parameters.length > limits.maxParameters
        ? { kind: 'too-many', max: limits.maxParameters }
        : undefined,
    }),
    parameters: draft.parameters.map((parameter) => parameterIssues(parameter, isDuplicate(parameter.name))),
  }
}

export const hasIssues = (issues: SkillDraftIssues): boolean =>
  Object.keys(issues.fields).length > 0 || issues.parameters.some((row) => Object.keys(row).length > 0)
