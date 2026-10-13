// SPEC-013 §1 / FR-PROMPT-002. Any `input_type` the backend doesn't recognise degrades to `text`,
// so an unknown future type never breaks rendering — it just falls back to a plain field.
export const PROMPT_INPUT_TYPES = ['text', 'number', 'select', 'checkbox'] as const

export type PromptInputType = (typeof PROMPT_INPUT_TYPES)[number]

const KNOWN_INPUT_TYPES: ReadonlySet<string> = new Set(PROMPT_INPUT_TYPES)

export const normalizeInputType = (raw: string | null | undefined): PromptInputType =>
  raw !== null && raw !== undefined && KNOWN_INPUT_TYPES.has(raw) ? (raw as PromptInputType) : 'text'
