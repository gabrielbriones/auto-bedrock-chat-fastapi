import type { SocketConnection } from '@/shared/ws/socket-client'

export type FakeChatSocketStep =
  | { readonly type: 'open' }
  | { readonly type: 'frame'; readonly data: string }
  | { readonly type: 'malformed-json' }
  | { readonly type: 'unknown-type' }
  | { readonly type: 'abrupt-close' }
  | { readonly type: 'close-during-send' }

// Deterministic WebSocket substitute for transport and message-bus tests.
export class FakeChatSocket implements SocketConnection {
  onclose: ((event: CloseEvent) => void) | null = null
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null
  onopen: ((event: Event) => void) | null = null
  #closed = false
  #closeDuringNextSend = false
  #nextStepIndex = 0
  readonly #steps: readonly FakeChatSocketStep[]
  readonly sent: string[] = []

  constructor(steps: readonly FakeChatSocketStep[] = []) {
    this.#steps = steps
  }

  close(): void {
    this.#close()
  }

  play(): void {
    while (this.playNext()) {
      // Playback intentionally advances until the script ends or the socket closes.
    }
  }

  playNext(): boolean {
    if (this.#closed) {
      return false
    }

    const step = this.#steps[this.#nextStepIndex]
    if (step === undefined) {
      return false
    }

    this.#nextStepIndex += 1
    switch (step.type) {
      case 'open':
        this.open()
        break
      case 'frame':
        this.receive(step.data)
        break
      case 'malformed-json':
        this.receive('{invalid json')
        break
      case 'unknown-type':
        this.receive(JSON.stringify({ type: 'unknown_frame', timestamp: '2026-08-25T12:00:00Z' }))
        break
      case 'abrupt-close':
        this.#close()
        break
      case 'close-during-send':
        this.#closeDuringNextSend = true
        break
    }

    return true
  }

  send(message: string): void {
    if (this.#closed) {
      throw new Error('socket is closed')
    }

    this.sent.push(message)
    if (this.#closeDuringNextSend) {
      this.#closeDuringNextSend = false
      this.#close()
      throw new Error('socket closed while sending')
    }
  }

  private open(): void {
    if (!this.#closed) {
      this.onopen?.({} as Event)
    }
  }

  private receive(data: unknown): void {
    if (!this.#closed) {
      this.onmessage?.({ data } as MessageEvent<unknown>)
    }
  }

  #close(): void {
    if (this.#closed) {
      return
    }

    this.#closed = true
    this.onclose?.({} as CloseEvent)
  }
}