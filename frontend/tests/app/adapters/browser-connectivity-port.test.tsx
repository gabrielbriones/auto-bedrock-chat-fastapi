import { describe, expect, it } from '@jest/globals'

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

  it.each([
    [false, 'offline'],
    [true, 'online'],
  ] as const)('maps navigator.onLine=%s to %s', (onLine, expected) => {
    const original = Object.getOwnPropertyDescriptor(Navigator.prototype, 'onLine')
    Object.defineProperty(navigator, 'onLine', { value: onLine, configurable: true })

    try {
      expect(new BrowserConnectivityPort().status()).toBe(expected)
    } finally {
      delete (navigator as { onLine?: boolean }).onLine
      if (original !== undefined) Object.defineProperty(Navigator.prototype, 'onLine', original)
    }
  })
})
