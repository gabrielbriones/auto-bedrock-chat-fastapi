import type { ComposedPrompt } from '@/domains/prompt-catalog/domain/composed-prompt'
import type { UrlState } from '@/domains/prompt-catalog/domain/deep-link'

export type { UrlState }

// SPEC-013 §3: the anti-corruption boundary. Prompt Catalog never imports Messaging beyond this —
// it hands over a `ComposedPrompt` and knows nothing about turns or sockets.
export interface ComposedPromptSink {
  submit(prompt: ComposedPrompt): void
}

// FR-TOOL-019: the concrete adapter lives in `src/app` (a `URL_STATE_OWNERS` file) — this context
// only ever sees the port.
export interface UrlNavigator {
  current(): UrlState
  replace(next: UrlState): void
}
