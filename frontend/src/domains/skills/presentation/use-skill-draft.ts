import { useRef, useState } from 'react'

import {
  contentFromDraft,
  draftFromSkill,
  EMPTY_SKILL_DRAFT,
  moveParameter,
  newParameterDraft,
  syncParametersWithInstructions,
  type Skill,
  type SkillDraft,
  type SkillParameterDraft,
} from '@/domains/skills/domain/public'

const contentKey = (draft: SkillDraft, enabled: boolean): string =>
  JSON.stringify([draft.name, enabled, contentFromDraft(draft)])

// Editor-local draft state. Dirtiness compares what would be sent, so typing and undoing a change
// (or leaving a blank options line) does not count as an edit.
export const useSkillDraft = (skill: Skill | null) => {
  const [initial] = useState(() => (skill === null ? EMPTY_SKILL_DRAFT : draftFromSkill(skill)))
  const [draft, setDraft] = useState<SkillDraft>(initial)
  const [enabled, setEnabled] = useState(true)
  const nextKey = useRef(0)
  const newKey = () => {
    nextKey.current += 1
    return `new-${nextKey.current}`
  }

  const patch = (change: Partial<SkillDraft>) => setDraft((current) => ({ ...current, ...change }))
  const setParameters = (update: (parameters: readonly SkillParameterDraft[]) => readonly SkillParameterDraft[]) =>
    setDraft((current) => ({ ...current, parameters: update(current.parameters) }))

  return {
    draft,
    enabled,
    setEnabled,
    patch,
    setInstructions: (instructions: string) => setDraft((current) => ({
      ...current,
      instructions,
      parameters: syncParametersWithInstructions(current.parameters, instructions, newKey),
    })),
    isDirty: () => contentKey(draft, enabled) !== contentKey(initial, true),
    changeParameter: (index: number, change: Partial<SkillParameterDraft>) =>
      setParameters((parameters) => parameters.map((parameter, at) => (at === index ? { ...parameter, ...change } : parameter))),
    moveParameter: (index: number, offset: -1 | 1) => setParameters((parameters) => moveParameter(parameters, index, offset)),
    removeParameter: (index: number) => setParameters((parameters) => parameters.filter((_, at) => at !== index)),
    addParameter: (name = '') => {
      const key = newKey()
      setParameters((parameters) => [...parameters, newParameterDraft(key, name)])
    },
  }
}
