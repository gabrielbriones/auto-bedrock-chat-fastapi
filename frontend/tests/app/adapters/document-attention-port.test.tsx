import { afterEach, describe, expect, it, jest } from '@jest/globals'

import { DocumentAttentionPort } from '@/app/adapters/document-attention-port'
import { LocalPreferenceStorage } from '@/app/adapters/local-preference-storage'

const setHidden = (hidden: boolean) => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
}

afterEach(() => {
  jest.restoreAllMocks()
  setHidden(false)
})

describe('DocumentAttentionPort (FR-MSG-032)', () => {
  it('is attended only when the tab is visible and its window has focus', () => {
    const port = new DocumentAttentionPort()
    const hasFocus = jest.spyOn(document, 'hasFocus')

    hasFocus.mockReturnValue(true)
    expect(port.isAttended()).toBe(true)

    hasFocus.mockReturnValue(false)
    expect(port.isAttended()).toBe(false)

    hasFocus.mockReturnValue(true)
    setHidden(true)
    expect(port.isAttended()).toBe(false)
  })

  it('reports visibility and focus changes until unsubscribed', () => {
    jest.spyOn(document, 'hasFocus').mockReturnValue(true)
    const listener = jest.fn()
    const unsubscribe = new DocumentAttentionPort().subscribe(listener)

    setHidden(true)
    document.dispatchEvent(new Event('visibilitychange'))
    setHidden(false)
    window.dispatchEvent(new Event('focus'))
    unsubscribe()
    window.dispatchEvent(new Event('blur'))

    expect(listener.mock.calls).toEqual([[false], [true]])
  })
})

describe('LocalPreferenceStorage', () => {
  it('round-trips through localStorage', () => {
    const storage = new LocalPreferenceStorage()

    storage.write('chat.test', 'false')

    expect(storage.read('chat.test')).toBe('false')
    expect(window.localStorage.getItem('chat.test')).toBe('false')
  })

  it('never throws when storage is unavailable', () => {
    jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    const storage = new LocalPreferenceStorage()

    expect(storage.read('chat.test')).toBeNull()
    expect(() => {
      storage.write('chat.test', 'true')
    }).not.toThrow()
  })
})
