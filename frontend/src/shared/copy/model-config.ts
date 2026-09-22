// FR-DS-011 / NFR-I18N-001 — user-facing `model-config` (`cfg`) copy.
export const MODEL_CONFIG_COPY = {
  sheet: {
    title: 'Model settings',
    description: 'Changes apply to this session and take effect once the server confirms them.',
    trigger: 'Model settings',
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
    enable_ai_summarization: 'Summarizes long tool results before they reach the model.',
    kb_similarity_threshold: 'Higher values require a closer match before a document is used.',
  },
  modelPicker: {
    trigger: 'Choose model',
    noTemperatureSupport: 'No temperature support',
  },
  pending: 'Waiting for server confirmation',
  reset: 'Reset to defaults',
  rejection: {
    title: 'A setting was rejected',
    dismiss: 'Dismiss rejection',
  },
  maxTokensClamped: (cap: number) => `Reduced to ${cap}, the selected model's limit.`,
} as const
