import { describe, expect, it, jest } from '@jest/globals'

import type { ServerFrame, ServerFrameSubscriber } from '@/shared/ws/message-bus'
import type { ConfigUpdatedEvent } from '@/domains/model-config/application/ports'
import { WsModelConfigGateway } from '@/domains/model-config/infrastructure/ws-model-config.gateway'

const harness = () => {
  const send = jest.fn<(frame: string) => 'sent'>(() => 'sent')
  let subscriber: ServerFrameSubscriber | undefined
  const gateway = new WsModelConfigGateway({ send }, {
    subscribe(next) {
      subscriber = next
      return () => { subscriber = undefined }
    },
  })
  const events: ConfigUpdatedEvent[] = []
  gateway.onUpdated((event) => events.push(event))

  return {
    gateway,
    events,
    sent: () => send.mock.calls.map(([frame]) => JSON.parse(frame) as object),
    emit: (frame: ServerFrame) => subscriber?.(frame),
  }
}

describe('WsModelConfigGateway', () => {
  it('writes update and reset frames using the backend contract', () => {
    const { gateway, sent } = harness()

    gateway.update({ temperature: 0.2 })
    gateway.reset()

    expect(sent()).toEqual([
      { type: 'config_update', config_overrides: { temperature: 0.2 }, override_mode: 'session' },
      { type: 'config_reset' },
    ])
  })

  it('maps valid override values and preserves server rejection reasons', () => {
    const { emit, events } = harness()

    emit({
      type: 'config_updated',
      timestamp: '2026-09-06T12:00:00Z',
      active_overrides: { temperature: 0.2, unknown: 'ignored' },
      applied_overrides: { temperature: 0.2 },
      rejected_overrides: ["'max_tokens' must be less than 4096", 42],
    })

    expect(events).toEqual([{
      activeOverrides: { temperature: 0.2 },
      appliedOverrides: { temperature: 0.2 },
      rejectedOverrides: ["'max_tokens' must be less than 4096"],
    }])
  })

  it('ignores frames owned by another context', () => {
    const { emit, events } = harness()
    emit({ type: 'pong', timestamp: '2026-09-06T12:00:00Z' })
    expect(events).toEqual([])
  })
})
