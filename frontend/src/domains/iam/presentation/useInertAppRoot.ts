import { useEffect } from 'react'

/**
 * FR-IAM-013. Base UI isolates a modal with focus guards plus a one-shot `aria-hidden` sweep of
 * whatever is in the document when it opens — it never uses `inert`, and the lazily loaded route
 * tree mounts into `#root` after the sweep has run, so the page behind the dialog would stay both
 * tabbable and announced. Inerting the app root covers it whenever it renders. The dialog itself
 * is portalled to `document.body`, so it is not affected.
 */
export function useInertAppRoot(inert: boolean) {
  useEffect(() => {
    const appRoot = document.getElementById('root')

    if (!inert || appRoot === null) {
      return
    }

    appRoot.setAttribute('inert', '')

    return () => {
      appRoot.removeAttribute('inert')
    }
  }, [inert])
}
