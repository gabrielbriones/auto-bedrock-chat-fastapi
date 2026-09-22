import { describe, expect, it, vi } from 'vitest'

import { FakeChatSocket } from './fake-chat-socket'

describe('FakeChatSocket', () => {
  it('plays open, frame, malformed JSON, unknown type, and abrupt-close steps synchronously', () => {
    const socket = new FakeChatSocket([
      { type: 'open' },
      { type: 'frame', data: '{"type":"pong"}' },
      { type: 'malformed-json' },
      { type: 'unknown-type' },
      { type: 'abrupt-close' },
    ])
    const onopen = vi.fn()
    const onmessage = vi.fn()
    const onclose = vi.fn()
    socket.onopen = onopen
    socket.onmessage = onmessage
    socket.onclose = onclose

    socket.play()

    expect(onopen).toHaveBeenCalledExactlyOnceWith(expect.anything())
    expect(onmessage).toHaveBeenNthCalledWith(1, expect.objectContaining({ data: '{"type":"pong"}' }))
    expect(onmessage).toHaveBeenNthCalledWith(2, expect.objectContaining({ data: '{invalid json' }))
    expect(onmessage).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({
        data: JSON.stringify({ type: 'unknown_frame', timestamp: '2026-08-25T12:00:00Z' }),
      }),
    )
    expect(onclose).toHaveBeenCalledExactlyOnceWith(expect.anything())
  })

  it('closes and throws while sending when its close-during-send fault is played', () => {
    const socket = new FakeChatSocket([{ type: 'close-during-send' }])
    const onclose = vi.fn()
    socket.onclose = onclose
    socket.play()

    expect(() => socket.send('frame')).toThrow('socket closed while sending')
    expect(socket.sent).toEqual(['frame'])
    expect(onclose).toHaveBeenCalledExactlyOnceWith(expect.anything())
  })

  it('advances scripted events one at a time without wall-clock waiting', () => {
    const socket = new FakeChatSocket([
      { type: 'open' },
      { type: 'frame', data: '{"type":"pong"}' },
    ])
    const onopen = vi.fn()
    const onmessage = vi.fn()
    socket.onopen = onopen
    socket.onmessage = onmessage

    expect(socket.playNext()).toBe(true)
    expect(onopen).toHaveBeenCalledOnce()
    expect(onmessage).not.toHaveBeenCalled()

    expect(socket.playNext()).toBe(true)
    expect(onmessage).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ data: '{"type":"pong"}' }),
    )
    expect(socket.playNext()).toBe(false)
  })
})