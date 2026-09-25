import { render } from '@testing-library/react'
import { describe, expect, it, jest } from '@jest/globals'

import type { Container } from '@/app/bootstrap/container'
import { ContainerContext } from '@/app/bootstrap/container-context'
import { fakeContainer } from '../bootstrap/container.fixture'
import { ChatSocketProvider } from '@/app/providers/ChatSocketProvider'
import type { SocketClient } from '@/shared/ws/socket-client'

const renderProvider = () => {
  const connect = jest.fn()
  const dispose = jest.fn()
  const socket = { connect, dispose } as unknown as SocketClient
  const container: Container = fakeContainer({ socket })

  const tree = (route: string) => (
    <ContainerContext.Provider value={container}>
      <ChatSocketProvider>
        <p>{route}</p>
      </ChatSocketProvider>
    </ContainerContext.Provider>
  )

  const view = render(tree('/bedrock-chat/ui'))

  return {
    ...view,
    connect,
    dispose,
    navigateTo(route: string) {
      view.rerender(tree(route))
    },
  }
}

describe('ChatSocketProvider (ADR-004)', () => {
  it('opens the container socket once on mount', () => {
    const { connect } = renderProvider()

    expect(connect).toHaveBeenCalledTimes(1)
  })

  it('keeps one socket for the application lifetime across route changes', () => {
    const { connect, dispose, navigateTo, getByText } = renderProvider()

    navigateTo('/bedrock-chat/ui/c/abc')
    navigateTo('/bedrock-chat/dashboard/feedback')

    expect(getByText('/bedrock-chat/dashboard/feedback')).toBeInTheDocument()
    expect(connect).toHaveBeenCalledTimes(1)
    expect(dispose).not.toHaveBeenCalled()
  })

  it('disposes the socket when the application unmounts', () => {
    const { dispose, unmount } = renderProvider()

    unmount()

    expect(dispose).toHaveBeenCalledTimes(1)
  })
})
