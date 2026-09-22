export type FeedbackInlineErrorProps = {
  readonly id: string
  readonly message: string
}

export function FeedbackInlineError({ id, message }: FeedbackInlineErrorProps) {
  return (
    <p id={id} role="alert" className="text-sm text-destructive">
      {message}
    </p>
  )
}