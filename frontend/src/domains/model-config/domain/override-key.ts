// DESIGN-001 §5.2 — the closed set of runtime generation overrides, parity with
// `OVERRIDABLE_LLM_PARAMS` ∪ the RAG/summarization toggle set.
export const OVERRIDE_KEYS = [
  'model_id',
  'temperature',
  'top_p',
  'max_tokens',
  'enable_ai_summarization',
  'enable_rag',
  'kb_top_k_results',
  'kb_similarity_threshold',
] as const

export type OverrideKey = (typeof OVERRIDE_KEYS)[number]

export type OverrideValue = string | number | boolean

export const isOverrideKey = (value: string): value is OverrideKey =>
  (OVERRIDE_KEYS as readonly string[]).includes(value)
