import { describe, expect, it } from 'vitest'

import { FRAME_OWNER, ServerFrameSchema } from '@/shared/ws/message-bus'

const fixtures: Record<string, unknown> = import.meta.glob('./fixtures/ws/*.json', {
  eager: true,
  import: 'default',
})

const fixtureFrameTypes = Object.keys(fixtures)
  .map((path) => path.replace(/^.*\/([^/]+)\.json$/, '$1'))
  .sort()

describe('WebSocket frame fixtures', () => {
  it('has one fixture for every server frame type', () => {
    expect(fixtureFrameTypes).toEqual(Object.keys(FRAME_OWNER).sort())
  })

  it.each(Object.entries(fixtures))('parses %s against the server-frame schema', (path, fixture) => {
    expect(ServerFrameSchema.safeParse(fixture), path).toMatchObject({ success: true })
  })
})