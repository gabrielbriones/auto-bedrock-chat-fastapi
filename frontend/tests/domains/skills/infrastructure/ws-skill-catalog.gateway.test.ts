import { describe, expect, it, jest } from '@jest/globals'

import { isOk } from '@/shared/kernel/result'
import type { ServerFrame } from '@/shared/ws/message-bus'

import { WsSkillCatalogGateway } from '@/domains/skills/infrastructure/ws-skill-catalog.gateway'
import catalogFrame from '../../../msw/fixtures/ws/skill_catalog.json' with { type: 'json' }

const setup = (sendResult: 'sent' | 'dropped-closed' = 'sent') => {
  const subscribers = new Set<(frame: ServerFrame) => void>()
  const send = jest.fn<(frame: string) => 'sent' | 'dropped-closed'>(() => sendResult)
  const gateway = new WsSkillCatalogGateway(
    { send },
    {
      subscribe: (subscriber) => {
        subscribers.add(subscriber)
        return () => subscribers.delete(subscriber)
      },
    },
  )
  const emit = (frame: ServerFrame) => subscribers.forEach((subscriber) => subscriber(frame))
  return { gateway, send, emit, subscribers }
}

describe('WsSkillCatalogGateway', () => {
  it('asks over the socket and parses the reply', async () => {
    const { gateway, send, emit, subscribers } = setup()

    const pending = gateway.list(new AbortController().signal)
    emit({ type: 'pong', timestamp: 't' })
    emit(catalogFrame as ServerFrame)
    const result = await pending

    expect(send).toHaveBeenCalledWith(JSON.stringify({ type: 'skill_catalog' }))
    expect(isOk(result) && result.value.map((entry) => entry.name)).toEqual(['job-triage'])
    expect(subscribers.size).toBe(0)
  })

  it('reports a refusal with the server code', async () => {
    const { gateway, emit } = setup()

    const pending = gateway.list(new AbortController().signal)
    emit({ type: 'skill_catalog', timestamp: 't', items: [], error: { code: 'authentication_required', message: 'Sign in' } })

    expect(await pending).toMatchObject({ error: { serverCode: 'authentication_required' } })
  })

  it('fails at once when the socket is closed', async () => {
    const { gateway, subscribers } = setup('dropped-closed')

    expect(await gateway.list(new AbortController().signal)).toMatchObject({ error: { code: 'network-error' } })
    expect(subscribers.size).toBe(0)
  })

  it('stops waiting when aborted', async () => {
    const { gateway, subscribers } = setup()
    const controller = new AbortController()

    const pending = gateway.list(controller.signal)
    controller.abort()

    expect(await pending).toMatchObject({ error: { code: 'aborted' } })
    expect(subscribers.size).toBe(0)
  })
})
