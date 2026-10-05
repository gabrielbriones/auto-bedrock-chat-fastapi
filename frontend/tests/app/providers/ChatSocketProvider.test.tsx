import { StrictMode } from 'react'
import { act, render } from '@testing-library/react'
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

  const view = render(tree('/chat/ui'))

  return {
    ...view,
    connect,
    dispose,
    navigateTo(route: string) {
      view.rerender(tree(route))
    },
  }
}

const flushConnection = async () => {
  await act(async () => {
    await Promise.resolve()
  })
}

describe('ChatSocketProvider (ADR-004)', () => {
  it('keeps one socket for the application lifetime across route changes', async () => {
    const { connect, dispose, navigateTo, getByText } = renderProvider()

    await flushConnection()

    navigateTo('/chat/ui/c/abc')
    navigateTo('/chat/dashboard/feedback')

    expect(getByText('/chat/dashboard/feedback')).toBeInTheDocument()
    expect(connect).toHaveBeenCalledTimes(1)
    expect(dispose).not.toHaveBeenCalled()
  })

  it('disposes the socket when the application unmounts', async () => {
    const { dispose, unmount } = renderProvider()

    await flushConnection()

    unmount()

    expect(dispose).toHaveBeenCalledTimes(1)
  })

  it('opens just one chat connection during the development StrictMode effect replay', async () => {
    const connect = jest.fn()
    const dispose = jest.fn()
    const container = fakeContainer({ socket: { connect, dispose } as unknown as SocketClient })
    const view = render(
      <StrictMode>
        <ContainerContext.Provider value={container}>
          <ChatSocketProvider><p>chat</p></ChatSocketProvider>
        </ContainerContext.Provider>
      </StrictMode>,
    )

    expect(connect).not.toHaveBeenCalled()
    expect(dispose).not.toHaveBeenCalled()

    await flushConnection()
    expect(connect).toHaveBeenCalledTimes(1)
    expect(dispose).not.toHaveBeenCalled()

    view.unmount()
    expect(dispose).toHaveBeenCalledTimes(1)
  })

  it('does not connect after unmounting before the deferred opening', async () => {
    const { connect, dispose, unmount } = renderProvider()

    unmount()
    await flushConnection()

    expect(connect).not.toHaveBeenCalled()
    expect(dispose).not.toHaveBeenCalled()
  })
})
