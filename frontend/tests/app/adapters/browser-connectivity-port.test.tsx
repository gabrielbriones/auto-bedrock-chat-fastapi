import { describe, expect, it } from 'vitest'

import { BrowserConnectivityPort } from '@/app/adapters/browser-connectivity-port'

describe('BrowserConnectivityPort', () => {
  it('reports transitions from the browser events, and stops after unsubscribing', () => {
    const connectivity = new BrowserConnectivityPort()
    const seen: string[] = []
    const unsubscribe = connectivity.subscribe((status) => seen.push(status))

    window.dispatchEvent(new Event('offline'))
    window.dispatchEvent(new Event('online'))
    unsubscribe()
    window.dispatchEvent(new Event('offline'))

    expect(seen).toEqual(['offline', 'online'])
  })

  it('reads the current state from the navigator', () => {
    expect(new BrowserConnectivityPort().status()).toBe(navigator.onLine ? 'online' : 'offline')
  })
})
