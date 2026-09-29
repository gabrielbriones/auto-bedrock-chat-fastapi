export type PresetDisabledReasonProps = {
  readonly id: string
  readonly message: string
}

// FR-PROMPT-012: a disabled preset states why, associated to its control via aria-describedby
// rather than left as a bare disabled button.
export function PresetDisabledReason({ id, message }: PresetDisabledReasonProps) {
  return (
    <p id={id} className="sr-only">
      {message}
    </p>
  )
}
