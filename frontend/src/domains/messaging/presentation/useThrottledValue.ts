import { useEffect, useRef, useState } from 'react'

/**
 * NFR-A11Y-006. A streaming turn produces a snapshot every few tens of milliseconds; announcing
 * each one turns a screen reader into noise. Leading edge plus trailing edge: the first change is
 * emitted at once, and no more than one further change lands per interval.
 */
export const useThrottledValue = <T,>(value: T, intervalMs: number): T => {
  const [throttled, setThrottled] = useState(value)
  const emittedAt = useRef(0)

  useEffect(() => {
    const wait = Math.max(0, emittedAt.current + intervalMs - Date.now())
    const timer = setTimeout(() => {
      emittedAt.current = Date.now()
      setThrottled(value)
    }, wait)

    return () => {
      clearTimeout(timer)
    }
  }, [value, intervalMs])

  return throttled
}
