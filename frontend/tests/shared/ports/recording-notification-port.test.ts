import { describe, expect, it } from 'vitest'

import { RecordingNotificationPort } from './recording-notification-port'

describe('RecordingNotificationPort', () => {
  it('records what the app told the user, in order and by kind', () => {
    const notifications = new RecordingNotificationPort()

    notifications.success('Saved')
    notifications.error('Failed', { description: 'Try later' })

    expect(notifications.messages()).toEqual(['Saved', 'Failed'])
    expect(notifications.notifications[1]).toEqual({
      kind: 'error',
      message: 'Failed',
      options: { description: 'Try later' },
    })
  })
})
