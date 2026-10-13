// FR-DS-011 / NFR-I18N-001 — user-facing `model-config` (`cfg`) copy.
export const MODEL_CONFIG_COPY = {
  dialog: {
    title: 'Model settings',
    close: 'Close',
    open: 'Model settings…',
  },
  fields: {
    model_id: 'Model',
    temperature: 'Temperature',
    top_p: 'Top P',
    max_tokens: 'Max tokens',
    enable_ai_summarization: 'AI summarization',
    enable_rag: 'Knowledge base retrieval',
    kb_top_k_results: 'Knowledge base results',
    kb_similarity_threshold: 'Knowledge base similarity threshold',
  },
  help: {
    temperature: 'Controls randomness. Lower values give focused, repeatable answers; higher values give more varied ones.',
    top_p: 'Limits word choices to the most likely ones whose probabilities add up to this value. Lower is more conservative.',
    max_tokens: 'The longest a single response can be, in tokens (roughly ¾ of a word each).',
    enable_ai_summarization: 'Summarizes long tool results before they reach the model.',
    enable_rag: 'Lets the assistant search the knowledge base for relevant documents before answering.',
    kb_top_k_results: 'How many knowledge base documents are given to the assistant per question.',
    kb_similarity_threshold: 'Higher values require a closer match before a document is used.',
  },
  modelPicker: {
    trigger: 'Choose model',
  },
  region: {
    label: 'Region',
    default: 'Default',
    names: { us: 'US', eu: 'EU', au: 'Australia', jp: 'Japan', global: 'Global' },
  },
  pending: 'Waiting for server confirmation',
  helpLabel: 'More information',
  reset: 'Reset to defaults',
  rejection: {
    title: 'A setting was rejected',
    dismiss: 'Dismiss rejection',
  },
  maxTokensClamped: (cap: number) => `Reduced to ${cap}, the selected model's limit.`,
} as const
