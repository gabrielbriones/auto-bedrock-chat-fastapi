import { describe, expect, it } from '@jest/globals'
import { render, screen } from '@testing-library/react'
import { act } from 'react'

import { OfflineBanner } from '@/components/ui/composed/offline-banner'
import { FakeConnectivityPort } from '../../../shared/ports/fake-connectivity-port'
import { SHELL } from '@/shared/copy/shell'

describe('OfflineBanner', () => {
  it('announces the outage and says reconnection is paused', () => {
    render(<OfflineBanner connectivity={new FakeConnectivityPort('offline')} />)

    expect(screen.getByRole('status')).toHaveTextContent(SHELL.offline)
  })

  it('appears and disappears as connectivity changes', () => {
    const connectivity = new FakeConnectivityPort('online')
    render(<OfflineBanner connectivity={connectivity} />)
    expect(screen.queryByText(SHELL.offline)).not.toBeInTheDocument()

    act(() => {
      connectivity.set('offline')
    })
    expect(screen.getByText(SHELL.offline)).toBeInTheDocument()

    act(() => {
      connectivity.set('online')
    })
    expect(screen.queryByText(SHELL.offline)).not.toBeInTheDocument()
  })
})
