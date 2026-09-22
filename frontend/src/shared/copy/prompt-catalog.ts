// FR-DS-011 / NFR-I18N-001 — user-facing `prompt` (prompt catalog) copy. SPEC-013 Phase 1 only
// (the variable panel); the preset bar and its disabled-reason help arrive with Phase 2.
export const PROMPT_CATALOG_COPY = {
  variablePanel: {
    label: 'Prompt variables',
  },
  selectPlaceholder: (label: string) => `Select ${label}…`,
  bar: {
    label: 'Preset prompts',
    otherGroup: 'Other',
    // FR-PROMPT-012: named variables read from their own PromptVariable.label, never raw names.
    disabledReason: (labels: readonly string[]) => `Missing or invalid: ${labels.join(', ')}.`,
  },
  detectedBadge: 'Detected',
  errors: {
    // FR-PROMPT-004: reused verbatim from src/shared/copy/iam.ts — the same validation failure
    // reads the same way everywhere in the app.
    required: 'This field is required.',
    numberInvalid: 'Enter a valid number.',
    numberRange: (min: number, max: number) => `Must be between ${min} and ${max}.`,
    numberMin: (min: number) => `Must be at least ${min}.`,
    numberMax: (max: number) => `Must be at most ${max}.`,
  },
} as const
