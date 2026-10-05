import { useEffect, type ReactNode } from 'react'

import { useContainer } from '@/app/bootstrap/container-context'

export type ChatSocketProviderProps = {
  readonly children: ReactNode
}

// ADR-004: one WebSocket for the application lifetime. It is mounted above the router, so route
// changes never reach this effect and never drop the connection. The container constructs the
// socket inert; opening and disposing it are this provider's only job.
export function ChatSocketProvider({ children }: ChatSocketProviderProps) {
  const { socket } = useContainer()

  useEffect(() => {
    let cancelled = false
    let connected = false

    // StrictMode replays mount/cleanup in development. Defer opening until after that replay
    // so its throwaway effect never creates a WebSocket the browser must immediately close.
    queueMicrotask(() => {
      if (!cancelled) {
        connected = true
        socket.connect()
      }
    })

    return () => {
      cancelled = true
      if (connected) {
        socket.dispose()
      }
    }
  }, [socket])

  return children
}
