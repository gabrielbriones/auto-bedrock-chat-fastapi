import type { KbCitation } from '@/domains/messaging/domain/turn'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

export type KbCitationsProps = {
  readonly citations: readonly KbCitation[]
}

const safeUrl = (value: string | null): string | null => {
  if (value === null) {
    return null
  }
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? value : null
  } catch {
    return null
  }
}

/** Displays knowledge-base citations in the server-provided ranking order. */
export function KbCitations({ citations }: KbCitationsProps) {
  if (citations.length === 0) {
    return null
  }

  return (
    <section aria-label={MESSAGING_COPY.transcript.sources} className="max-w-prose border-t border-border pt-3 text-sm">
      <h3 className="font-medium">{MESSAGING_COPY.transcript.sources}</h3>
      <ol className="mt-2 list-decimal space-y-1 pl-5">
        {citations.map((citation, index) => {
          const label = citation.title ?? citation.source ?? MESSAGING_COPY.transcript.source(index + 1)
          const url = safeUrl(citation.url)
          return (
            <li key={`${citation.documentId ?? citation.source ?? 'source'}-${index}`}>
              {url === null ? (
                label
              ) : (
                <a href={url} rel="noopener noreferrer" target="_blank">
                  {label}
                </a>
              )}
            </li>
          )
        })}
      </ol>
    </section>
  )
}