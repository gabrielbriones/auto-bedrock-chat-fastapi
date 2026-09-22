import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { shellScrollContainer } from '@/components/ui/composed/scroll-container'

// Sub-pixel layout means a viewport pinned to the bottom rarely reports an exact match.
const BOTTOM_SLACK_PX = 24

const isAtBottom = (element: HTMLElement): boolean =>
  element.scrollHeight - element.scrollTop - element.clientHeight <= BOTTOM_SLACK_PX

const toBottom = (element: HTMLElement): void => {
  element.scrollTop = element.scrollHeight
}

export type StickToBottom = {
  /** The viewport is pinned to the newest message and will follow it. */
  readonly stuck: boolean
  /** Messages that arrived while the reader was scrolled away. */
  readonly unread: number
  readonly jumpToLatest: () => void
}

const useScrollWatch = (onMove: (atBottom: boolean) => void): void => {
  useEffect(() => {
    const scroller = shellScrollContainer()

    if (scroller === null) {
      return
    }

    const onScroll = () => {
      onMove(isAtBottom(scroller))
    }

    scroller.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      scroller.removeEventListener('scroll', onScroll)
    }
  }, [onMove])
}

/**
 * FIX-12. The legacy client scrolled on every message, so reading scrollback while a response
 * streamed was impossible. Here sticking is a mode the reader controls: scrolling up leaves it and
 * jumping re-arms it. Stable entry keys distinguish appended or streaming content from a replaced
 * transcript: only replacement forces the reader back to the latest message.
 */
export const useStickToBottom = (
  revision: readonly { readonly key: string }[],
  count: number,
): StickToBottom => {
  const [stuck, setStuck] = useState(true)
  const [unread, setUnread] = useState(0)
  // The scroll listener and the layout effect both read these between renders.
  const sticking = useRef(true)
  const seen = useRef(count)
  const previousRevision = useRef(revision)

  const stick = useCallback((value: boolean) => {
    sticking.current = value
    setStuck(value)

    if (value) {
      setUnread(0)
    }
  }, [])

  const jumpToLatest = useCallback(() => {
    const scroller = shellScrollContainer()

    if (scroller !== null) {
      toBottom(scroller)
    }

    stick(true)
  }, [stick])

  useScrollWatch(stick)

  // Layout, not passive: the follow has to happen before the browser paints the taller content,
  // otherwise every snapshot shows a one-frame jump.
  useLayoutEffect(() => {
    const replaced = previousRevision.current.some(
      (entry, index) => revision[index]?.key !== entry.key,
    )
    previousRevision.current = revision
    const arrived = count - seen.current
    seen.current = count

    const scroller = shellScrollContainer()

    if (scroller === null) {
      return
    }

    if (replaced) {
      toBottom(scroller)
      stick(true)
      return
    }

    if (sticking.current) {
      toBottom(scroller)
      return
    }

    if (arrived > 0) {
      setUnread((previous) => previous + arrived)
    }
    // `revision` is the trigger: `count` is read rather than depended on, so a snapshot that grows
    // an existing message still re-pins.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision, stick])

  return { stuck, unread, jumpToLatest }
}
