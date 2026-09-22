import { useEffect } from 'react'

// FR-SHELL-024: every route sets a document title. Callers compose it — the app title itself comes
// from the bootstrap payload, so nothing at this layer knows about it.
export const useDocumentTitle = (title: string): void => {
  useEffect(() => {
    document.title = title
  }, [title])
}
