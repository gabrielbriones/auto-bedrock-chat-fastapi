import type { PageAttentionPort } from '@/shared/ports/page-attention-port'

// FR-MSG-032. `document.hidden` covers a background tab or a minimised window; `hasFocus()`
// covers a visible window that another application is in front of.
export class DocumentAttentionPort implements PageAttentionPort {
  isAttended(): boolean {
    if (typeof document === 'undefined') {
      return true
    }

    return !document.hidden && document.hasFocus()
  }

  subscribe(listener: (attended: boolean) => void): () => void {
    if (typeof window === 'undefined') {
      return () => {}
    }

    const onChange = () => {
      listener(this.isAttended())
    }

    document.addEventListener('visibilitychange', onChange)
    window.addEventListener('focus', onChange)
    window.addEventListener('blur', onChange)

    return () => {
      document.removeEventListener('visibilitychange', onChange)
      window.removeEventListener('focus', onChange)
      window.removeEventListener('blur', onChange)
    }
  }
}
