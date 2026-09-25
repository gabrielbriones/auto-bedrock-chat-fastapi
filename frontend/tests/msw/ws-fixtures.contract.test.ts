import { describe, expect, it } from '@jest/globals'

import { FRAME_OWNER, ServerFrameSchema } from '@/shared/ws/message-bus'

import { loadWsFixtures } from './fixtures/ws/load'

const fixtures: Record<string, unknown> = loadWsFixtures()

const fixtureFrameTypes = Object.keys(fixtures)
  .map((path) => path.replace(/^.*\/([^/]+)\.json$/, '$1'))
  .sort()

describe('WebSocket frame fixtures', () => {
  it('has one fixture for every server frame type', () => {
    expect(fixtureFrameTypes).toEqual(Object.keys(FRAME_OWNER).sort())
  })

  it.each(Object.entries(fixtures))('parses %s against the server-frame schema', (_path, fixture) => {
    expect(ServerFrameSchema.safeParse(fixture)).toMatchObject({ success: true })
  })
})