import type { SkillParameter } from '@/domains/skills/domain/skill'

export type PreviewSegment =
  | { readonly kind: 'text'; readonly text: string }
  | { readonly kind: 'value'; readonly name: string; readonly text: string }
  // A `{{name}}` no parameter declares: kept verbatim so the admin can spot the typo.
  | { readonly kind: 'unknown'; readonly name: string; readonly text: string }

const PLACEHOLDER = /\{\{\s*([A-Za-z0-9_-]+)\s*\}\}/g

// Each distinct `{{name}}` in the instructions, in order of first appearance.
export const placeholderNames = (instructions: string): readonly string[] =>
  [...new Set(Array.from(instructions.matchAll(PLACEHOLDER), (match) => match[1] ?? ''))]

// The most realistic string the admin already wrote for the parameter, falling back to its name.
export const sampleValue = (parameter: SkillParameter): string =>
  [parameter.example, parameter.default ?? '', parameter.placeholder, parameter.options[0] ?? '', parameter.label]
    .find((candidate) => candidate !== '') ?? parameter.name

// Splits the instructions into plain text and substituted sample values. The result is data, not
// markup: the preview renders every segment as a text node.
export const renderPreview = (
  instructions: string,
  parameters: readonly SkillParameter[],
): readonly PreviewSegment[] => {
  const byName = new Map(parameters.map((parameter) => [parameter.name, parameter]))
  const segments: PreviewSegment[] = []
  let cursor = 0

  for (const match of instructions.matchAll(PLACEHOLDER)) {
    const [raw, name = ''] = match
    if (match.index > cursor) {
      segments.push({ kind: 'text', text: instructions.slice(cursor, match.index) })
    }
    const parameter = byName.get(name)
    segments.push(parameter === undefined
      ? { kind: 'unknown', name, text: raw }
      : { kind: 'value', name, text: sampleValue(parameter) })
    cursor = match.index + raw.length
  }

  if (cursor < instructions.length) {
    segments.push({ kind: 'text', text: instructions.slice(cursor) })
  }
  return segments
}
