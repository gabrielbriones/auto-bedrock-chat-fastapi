import { CONVERSATION_COPY } from '@/shared/copy/conversation'

import type { ConversationErrorCode } from '@/domains/conversation/domain/events'

// FR-CONV-019. Four documented codes, each with a different consequence, and an open fallback so a
// code the backend adds later still reaches the user as its own message instead of being swallowed.
export type ConversationErrorEffect =
  /** Shown in place, next to the roster — the history call failed, the session did not. */
  | { readonly kind: 'transient'; readonly message: string }
  | { readonly kind: 'toast-and-refresh'; readonly message: string }
  /** The server has persistence off: the sidebar is hidden for the rest of the session. */
  | { readonly kind: 'disable-persistence'; readonly message: string }
  | { readonly kind: 'toast'; readonly message: string }

export const conversationErrorEffect = (
  code: ConversationErrorCode,
  message: string,
): ConversationErrorEffect => {
  switch (code) {
    case 'conversation_history_unavailable':
      return { kind: 'transient', message: CONVERSATION_COPY.errors.transient(message) }
    case 'conversation_not_found':
      return { kind: 'toast-and-refresh', message: CONVERSATION_COPY.errors.notFound }
    case 'conversation_persistence_disabled':
      return { kind: 'disable-persistence', message: CONVERSATION_COPY.errors.persistenceDisabled }
    case 'invalid_conversation_request':
      return { kind: 'toast', message: CONVERSATION_COPY.errors.invalidRequest }
    default:
      return { kind: 'toast', message }
  }
}
