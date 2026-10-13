export type ConfirmRequest = {
  readonly title: string
  readonly message: string
  readonly confirmLabel?: string
  readonly tone?: 'default' | 'destructive'
}

export type PromptRequest = {
  readonly title: string
  readonly message?: string
  readonly label: string
  readonly initialValue?: string
  readonly confirmLabel?: string
  // FR-SHELL-021: an empty submission is accepted and resolves to '' — the caller decides what to
  // do with it (the rollback reason asks a second time).
  readonly optional?: boolean
}

// SPEC-021 §5 / FR-SHELL-006: the only way to ask the user anything. Neither method rejects;
// cancelling resolves `false` / `null`, so no caller needs a try/catch.
export interface ConfirmationPort {
  confirm(request: ConfirmRequest): Promise<boolean>
  prompt(request: PromptRequest): Promise<string | null>
}
