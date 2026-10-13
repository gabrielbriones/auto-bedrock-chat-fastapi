// T-079 / FR-MSG-010…012a. The composer's two decisions — "may I type?" and "what does this
// keystroke mean?" — are pure functions so the matrix can be enumerated exhaustively without a DOM.

export type ComposerAvailability = {
  readonly enabled: boolean
  /** Why the composer is closed, shown to the user. `null` exactly when `enabled`. */
  readonly reason: string | null
}

export type ComposerConditions = {
  /** FR-MSG-012a: the IAM session permits composing (`requireAuth === false` or authenticated). */
  readonly inputEnabled: boolean
  /** FR-MSG-024: the socket is open. */
  readonly connected: boolean
  readonly awaitingResponse: boolean
  /** `lockInputWhileResponding` from the bootstrap config (FR-MSG-012). */
  readonly lockWhileResponding: boolean
}

export type ComposerReasons = {
  readonly unauthenticated: string
  readonly offline: string
  readonly responding: string
}

// Ordered most-fundamental first: an unauthenticated user is told about auth even while the socket
// is also down, because reconnecting would not help them.
export const composerAvailability = (
  conditions: ComposerConditions,
  reasons: ComposerReasons,
): ComposerAvailability => {
  if (!conditions.inputEnabled) {
    return { enabled: false, reason: reasons.unauthenticated }
  }

  if (!conditions.connected) {
    return { enabled: false, reason: reasons.offline }
  }

  // A turn that recycles into an interruption is no longer awaiting, so this unlocks with it.
  if (conditions.awaitingResponse && conditions.lockWhileResponding) {
    return { enabled: false, reason: reasons.responding }
  }

  return { enabled: true, reason: null }
}

/** `newline` covers both the native Shift+Enter and the modifiers a browser would otherwise eat. */
export type ComposerKeyIntent = 'send' | 'newline' | 'ignore' | 'pass'

export type ComposerKeystroke = {
  readonly key: string
  readonly shiftKey: boolean
  readonly ctrlKey: boolean
  readonly metaKey: boolean
  readonly altKey: boolean
  /** Mid-IME-composition: the Enter that commits a candidate must never send (FR-MSG-010). */
  readonly isComposing: boolean
}

export type ComposerKeyContext = {
  readonly locked: boolean
  readonly hasText: boolean
}

export const composerKeyIntent = (
  keystroke: ComposerKeystroke,
  context: ComposerKeyContext,
): ComposerKeyIntent => {
  if (keystroke.key !== 'Enter') {
    return 'pass'
  }

  if (keystroke.isComposing) {
    return 'ignore'
  }

  if (keystroke.shiftKey || keystroke.ctrlKey || keystroke.metaKey || keystroke.altKey) {
    return 'newline'
  }

  // FR-MSG-009/012/025: a locked or empty composer swallows Enter rather than submitting nothing.
  return context.locked || !context.hasText ? 'ignore' : 'send'
}
