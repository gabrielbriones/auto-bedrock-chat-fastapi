import type { ReactNode } from 'react'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

const { welcome: COPY } = MESSAGING_COPY

export type WelcomeStateProps = {
  readonly message: string
  readonly children?: ReactNode
}

export function WelcomeState({ message, children }: WelcomeStateProps) {
  return (
    <section
      aria-label={COPY.label}
      className="m-auto flex w-full max-w-prose min-w-0 flex-col items-center gap-4 text-center"
    >
      <p className="text-muted-foreground">{message}</p>

      {children}
    </section>
  )
}
