import type { ReactNode } from 'react'
import { Sparkles } from 'lucide-react'
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
      className="mx-auto mt-[8vh] mb-auto flex w-full max-w-4xl min-w-0 flex-col items-center gap-8 py-8 text-center"
    >
      <div className="flex flex-col items-center gap-4">
        <div
          aria-hidden="true"
          className="flex size-12 items-center justify-center rounded-2xl bg-gradient-to-br from-primary/30 to-primary/5 shadow-lg shadow-primary/10 ring-1 ring-primary/30"
        >
          <Sparkles className="size-5 text-primary" />
        </div>
        <p className="max-w-2xl text-base leading-relaxed text-balance text-muted-foreground">{message}</p>
      </div>

      {children}
    </section>
  )
}
