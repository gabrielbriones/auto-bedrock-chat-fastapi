import { describe, expect, it } from '@jest/globals'

import {
  charCount,
  contentFromDraft,
  contentOf,
  diffContent,
  draftFromSkill,
  EMPTY_SKILL_DRAFT,
  filterSkills,
  hasIssues,
  moveParameter,
  newParameterDraft,
  syncParametersWithInstructions,
  toSummary,
  validateSkillDraft,
  type Skill,
  type SkillDraft,
  type SkillLimits,
} from '@/domains/skills/domain/public'

const limits: SkillLimits = { instructionsMaxChars: 50, descriptionMaxChars: 10, maxParameters: 2 }

const skill: Skill = {
  name: 'job-triage',
  displayName: 'Job triage',
  description: 'Triage',
  instructions: 'Look at {{job_id}}',
  parameters: [{
    name: 'job_id',
    label: 'Job',
    help: 'h',
    required: true,
    default: '',
    inputType: 'select',
    options: ['a', 'b'],
    placeholder: 'p',
    example: 'e',
  }],
  enabled: true,
  revision: 2,
  createdAt: null,
  updatedAt: null,
  updatedBy: null,
}

const valid: SkillDraft = { ...EMPTY_SKILL_DRAFT, name: 'new-skill', displayName: 'New', instructions: 'Do it' }

describe('skill drafts', () => {
  it('round-trips every stored parameter hint unchanged', () => {
    expect(contentFromDraft(draftFromSkill(skill))).toEqual(contentOf(skill))
  })

  it('drops blank option lines left while typing', () => {
    const draft = { ...valid, parameters: [{ ...newParameterDraft('k'), name: 'x', options: ['a', '', 'b', ''] }] }

    expect(contentFromDraft(draft).parameters[0]?.options).toEqual(['a', 'b'])
  })

  it('moves a parameter within bounds and ignores moves past the ends', () => {
    const rows = [newParameterDraft('a'), newParameterDraft('b')]

    expect(moveParameter(rows, 0, 1).map((row) => row.key)).toEqual(['b', 'a'])
    expect(moveParameter(rows, 0, -1)).toBe(rows)
    expect(moveParameter(rows, 1, 1)).toBe(rows)
  })
})

describe('syncParametersWithInstructions', () => {
  const keys = () => {
    let next = 0
    return () => `k${++next}`
  }

  it('adds a marked row for each new valid placeholder, once', () => {
    const rows = syncParametersWithInstructions([], 'Use {{job_id}} and {{job_id}} with {{Bad}}', keys())

    expect(rows.map((row) => [row.name, row.autoAdded])).toEqual([['job_id', true]])
  })

  it('drops an untouched auto-added row once its placeholder is gone, so typing inside {{}} leaves no trail', () => {
    const next = keys()
    const first = syncParametersWithInstructions([], '{{a}}', next)
    const second = syncParametersWithInstructions(first, '{{ar}}', next)

    expect(second.map((row) => row.name)).toEqual(['ar'])
  })

  it('keeps an auto-added row the admin edited, and never touches manual rows', () => {
    const edited = { ...newParameterDraft('e', 'job_id', true), label: 'Job' }
    const manual = { ...newParameterDraft('m'), name: 'unused' }

    expect(syncParametersWithInstructions([edited, manual], 'nothing', keys())).toEqual([edited, manual])
  })

  it('returns the same rows when nothing changes', () => {
    const rows = [newParameterDraft('a', 'x', true)]

    expect(syncParametersWithInstructions(rows, '{{x}}', keys())).toBe(rows)
  })
})

describe('validateSkillDraft', () => {
  it('reports placeholders no parameter declares', () => {
    expect(validateSkillDraft({ ...valid, instructions: 'Use {{job_id}} and {{Bad}}' }, limits, true).fields.instructions)
      .toEqual({ kind: 'unknown-placeholders', names: ['job_id', 'Bad'] })
  })

  it('accepts a minimal valid draft', () => {
    expect(hasIssues(validateSkillDraft(valid, limits, true))).toBe(false)
  })

  it('requires name, display name and non-blank instructions', () => {
    const issues = validateSkillDraft({ ...EMPTY_SKILL_DRAFT, instructions: '   ' }, limits, true)

    expect(issues.fields).toEqual({ name: { kind: 'required' }, displayName: { kind: 'required' }, instructions: { kind: 'required' } })
  })

  it('checks the name pattern only when creating', () => {
    expect(validateSkillDraft({ ...valid, name: 'Bad Name' }, limits, true).fields.name).toEqual({ kind: 'pattern' })
    expect(validateSkillDraft({ ...valid, name: 'Bad Name' }, limits, false).fields.name).toBeUndefined()
  })

  it('applies the server limits once they are known', () => {
    const draft = {
      ...valid,
      description: 'x'.repeat(11),
      instructions: 'x'.repeat(51),
      parameters: ['a', 'b', 'c'].map((name) => ({ ...newParameterDraft(name), name })),
    }

    expect(validateSkillDraft(draft, limits, true).fields).toEqual({
      description: { kind: 'too-long', max: 10 },
      instructions: { kind: 'too-long', max: 50 },
      parameters: { kind: 'too-many', max: 2 },
    })
    expect(hasIssues(validateSkillDraft(draft, null, true))).toBe(false)
  })

  it('flags display names over 100 characters', () => {
    expect(validateSkillDraft({ ...valid, displayName: 'x'.repeat(101) }, limits, true).fields.displayName)
      .toEqual({ kind: 'too-long', max: 100 })
  })

  it('counts characters as code points, like the server, so an emoji counts once', () => {
    expect(charCount('😀'.repeat(60))).toBe(60)
    expect(validateSkillDraft({ ...valid, displayName: '😀'.repeat(60) }, limits, true).fields.displayName).toBeUndefined()
    expect(validateSkillDraft({ ...valid, displayName: '😀'.repeat(101) }, limits, true).fields.displayName)
      .toEqual({ kind: 'too-long', max: 100 })
  })

  it('reports per-parameter issues: names, duplicates, hint lengths and options', () => {
    const draft = {
      ...valid,
      parameters: [
        { ...newParameterDraft('1'), name: 'dup', label: 'x'.repeat(101), default: 'x'.repeat(1001) },
        { ...newParameterDraft('2'), name: 'dup', options: Array.from({ length: 51 }, (_, index) => `o${index}`) },
        { ...newParameterDraft('3'), name: '', options: ['x'.repeat(201)], help: 'x'.repeat(501) },
        { ...newParameterDraft('4'), name: 'Bad!', placeholder: 'x'.repeat(201), example: 'x'.repeat(501) },
      ],
    }

    expect(validateSkillDraft(draft, null, true).parameters).toEqual([
      { name: { kind: 'duplicate' }, label: { kind: 'too-long', max: 100 }, default: { kind: 'too-long', max: 1000 } },
      { name: { kind: 'duplicate' }, options: { kind: 'too-many', max: 50 } },
      { name: { kind: 'required' }, options: { kind: 'too-long', max: 200 }, help: { kind: 'too-long', max: 500 } },
      { name: { kind: 'pattern' }, placeholder: { kind: 'too-long', max: 200 }, example: { kind: 'too-long', max: 500 } },
    ])
  })
})

describe('diffContent', () => {
  it('returns only the changed content fields', () => {
    const base = contentOf(skill)

    expect(diffContent(base, base)).toEqual({})
    expect(diffContent(base, { ...base, description: 'Other', parameters: [] })).toEqual({ description: 'Other', parameters: [] })
    expect(diffContent(base, { ...base, displayName: 'D', instructions: 'I' })).toEqual({ displayName: 'D', instructions: 'I' })
  })
})

describe('filterSkills', () => {
  const rows = [toSummary(skill), { ...toSummary(skill), name: 'perf', displayName: 'Performance', description: 'Runs' }]

  it('matches name, display name or description, case-insensitively', () => {
    expect(filterSkills(rows, '  ').length).toBe(2)
    expect(filterSkills(rows, 'PERF').map((row) => row.name)).toEqual(['perf'])
    expect(filterSkills(rows, 'triage').map((row) => row.name)).toEqual(['job-triage'])
    expect(filterSkills(rows, 'runs').map((row) => row.name)).toEqual(['perf'])
  })
})
