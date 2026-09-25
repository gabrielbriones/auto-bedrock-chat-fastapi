import { describe, expect, it } from '@jest/globals'

import { resolveSocketUrl } from '@/domains/messaging/domain/socket-url'

describe('resolveSocketUrl', () => {
  it('resolves a server-supplied path against the page origin', () => {
    expect(resolveSocketUrl('/bedrock-chat/ws', 'http://localhost:3000/ui/')).toBe(
      'ws://localhost:3000/bedrock-chat/ws',
    )
  })

  it('resolves a relative path against the page, not the origin root', () => {
    expect(resolveSocketUrl('ws', 'http://localhost:3000/bedrock-chat/')).toBe(
      'ws://localhost:3000/bedrock-chat/ws',
    )
  })

  it('uses wss on a secure page', () => {
    expect(resolveSocketUrl('/bedrock-chat/ws', 'https://analyzer.intel.com/ui/')).toBe(
      'wss://analyzer.intel.com/bedrock-chat/ws',
    )
  })

  it('keeps the host and path of an absolute url while forcing the page scheme', () => {
    expect(resolveSocketUrl('wss://chat.intel.com/ws', 'https://analyzer.intel.com/ui/')).toBe(
      'wss://chat.intel.com/ws',
    )
    expect(resolveSocketUrl('http://chat.test/ws', 'http://localhost:3000/ui/')).toBe(
      'ws://chat.test/ws',
    )
  })

  it('preserves a query string the server put there but never adds one', () => {
    expect(resolveSocketUrl('/ws?session_id=abc', 'http://localhost:3000/ui/')).toBe(
      'ws://localhost:3000/ws?session_id=abc',
    )
    expect(resolveSocketUrl('/ws', 'http://localhost:3000/ui/?token=leaked')).toBe(
      'ws://localhost:3000/ws',
    )
  })

  it('returns the raw value when there is no page to resolve against', () => {
    expect(resolveSocketUrl('/bedrock-chat/ws', '')).toBe('/bedrock-chat/ws')
  })
})
