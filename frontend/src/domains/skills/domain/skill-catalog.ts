import type { SkillParameter } from '@/domains/skills/domain/skill'

// `GET {chat}/skills/catalog`: an enabled skill as users see it, without its instructions.
export type SkillCatalogEntry = {
  readonly name: string
  readonly displayName: string
  readonly description: string
  readonly parameters: readonly SkillParameter[]
  readonly revision: number
}
