import { afterAll, afterEach, beforeAll, describe, expect, it, jest } from '@jest/globals'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { Logger } from '@/shared/logging/logger'
import { HttpClient } from '@/shared/http/http-client'

import { server } from '../../msw/server'
import {
  bootstrapHtml502Handler,
  bootstrapMalformedHandler,
} from '../../msw/handlers/bootstrap'
import { BootstrapProvider } from '@/app/bootstrap/BootstrapProvider'
import { useContainer } from '@/app/bootstrap/container-context'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const fakeLogger = (): Logger => ({ debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() })

function ChildProbe() {
  const container = useContainer()
  return <p>Ready: {container.bootstrap.adminPrefix}</p>
}

describe('BootstrapProvider', () => {
  it('renders only the loading state until bootstrap resolves, then renders children', async () => {
    render(
      <BootstrapProvider baseUrl="http://localhost/bedrock-chat" logger={fakeLogger()}>
        <ChildProbe />
      </BootstrapProvider>,
    )

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByText(/Ready:/)).not.toBeInTheDocument()

    await waitFor(() => expect(screen.getByText('Ready: /bedrock-chat/admin')).toBeInTheDocument())
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows a full-page error with a diagnostic reference on a malformed response, never a blank page', async () => {
    server.use(bootstrapMalformedHandler)

    render(
      <BootstrapProvider baseUrl="http://localhost/bedrock-chat" logger={fakeLogger()}>
        <ChildProbe />
      </BootstrapProvider>,
    )

    await waitFor(() => expect(screen.getByText('Invalid bootstrap configuration')).toBeInTheDocument())
    expect(screen.getByText(/Reference:/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByText(/Ready:/)).not.toBeInTheDocument()
  })

  it('shows the readable Problem title for a 502 with an HTML body, and Retry re-issues the request', async () => {
    server.use(bootstrapHtml502Handler)

    render(
      <BootstrapProvider baseUrl="http://localhost/bedrock-chat" httpClient={new HttpClient()} logger={fakeLogger()}>
        <ChildProbe />
      </BootstrapProvider>,
    )

    await waitFor(() => expect(screen.getByText('Bad Gateway')).toBeInTheDocument())

    server.resetHandlers()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(screen.getByText('Ready: /bedrock-chat/admin')).toBeInTheDocument())
  })
})
