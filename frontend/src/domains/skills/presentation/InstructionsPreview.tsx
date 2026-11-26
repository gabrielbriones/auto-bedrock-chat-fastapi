import { SKILLS_COPY } from '@/shared/copy/skills'

import { renderPreview, type SkillParameter } from '@/domains/skills/domain/public'

const COPY = SKILLS_COPY.preview

export type InstructionsPreviewProps = {
  readonly instructions: string
  readonly parameters: readonly SkillParameter[]
}

// Stored text is only ever rendered as text nodes: instructions are privileged content, and a
// preview must show exactly what the assistant will read, never interpret it as markup.
// Laid out like the instructions field beside it (label, box, hint) so the two line up row by row.
export function InstructionsPreview({ instructions, parameters }: InstructionsPreviewProps) {
  const segments = renderPreview(instructions, parameters)

  return (
    <section className="grid grid-rows-[auto_1fr_auto] gap-1.5" aria-labelledby="skill-preview-title" aria-describedby="skill-preview-description">
      <div className="flex min-h-4 items-end">
        <h3 id="skill-preview-title" className="text-sm leading-none font-medium">{COPY.title}</h3>
      </div>
      <div className="h-full min-h-64 overflow-y-auto rounded-lg border border-border bg-muted/40 px-2.5 py-2 font-mono text-sm break-words whitespace-pre-wrap" data-testid="skill-preview">
        {segments.length === 0 ? <span className="text-muted-foreground">{COPY.empty}</span> : segments.map((segment, index) => {
          switch (segment.kind) {
            case 'text':
              return <span key={index}>{segment.text}</span>
            case 'value':
              return <mark key={index} className="rounded-sm bg-accent px-0.5 text-accent-foreground">{segment.text}</mark>
            case 'unknown':
              return <span key={index} title={COPY.unknown(segment.name)} className="text-destructive underline decoration-wavy">{segment.text}</span>
          }
        })}
      </div>
      <p id="skill-preview-description" className="text-xs text-muted-foreground">{COPY.description}</p>
    </section>
  )
}
