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
    socket.connect()

    return () => {
      socket.dispose()
    }
  }, [socket])

  return children
}
