import { describe, expect, it } from '@jest/globals'

import { connectionView } from '@/domains/messaging/domain/connection'

describe('connectionView', () => {
  it('reports an open socket as connected', () => {
    expect(connectionView('open')).toEqual({ kind: 'connected' })
  })

  it('reports the states before a first open as connecting', () => {
    expect(connectionView('idle')).toEqual({ kind: 'connecting' })
    expect(connectionView('connecting')).toEqual({ kind: 'connecting' })
  })

  it('reports a reconnect attempt as disconnected, with no intermediate state', () => {
    expect(connectionView('reconnecting')).toEqual({ kind: 'disconnected' })
  })

  it('reports an intentional or exhausted close as disconnected', () => {
    expect(connectionView('closed')).toEqual({ kind: 'disconnected' })
  })
})
