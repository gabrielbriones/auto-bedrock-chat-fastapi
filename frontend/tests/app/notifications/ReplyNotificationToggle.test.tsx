import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from '@jest/globals'
import { axe } from 'jest-axe'

import { ContainerContext } from '@/app/bootstrap/container-context'
import { createReplyNotifications } from '@/app/bootstrap/reply-notifications'
import { ReplyNotificationToggle } from '@/app/notifications/ReplyNotificationToggle'
import { REPLY_NOTIFICATIONS_ENABLED_KEY } from '@/app/notifications/reply-notification.store'
import { REPLY_NOTIFICATION_COPY } from '@/shared/copy/reply-notifications'
import type { SystemNotificationPermission } from '@/shared/ports/system-notification-port'
import { fakeContainer } from '../bootstrap/container.fixture'
import { RecordingNotificationPort } from '../../shared/ports/recording-notification-port'
import {
  FakePageAttentionPort,
  FakeSystemNotificationPort,
  InMemoryPreferenceStorage,
} from '../../shared/ports/fake-reply-notification-ports'

const renderToggle = (permission: SystemNotificationPermission, enabled = true) => {
  const base = fakeContainer()
  const system = new FakeSystemNotificationPort(permission)
  const storage = new InMemoryPreferenceStorage()
  storage.write(REPLY_NOTIFICATIONS_ENABLED_KEY, String(enabled))
  const replyNotifications = createReplyNotifications(base.bootstrap, base, {
    systemNotifications: system,
    pageAttention: new FakePageAttentionPort(),
    preferenceStorage: storage,
  })
  const notifications = new RecordingNotificationPort()
  const container = fakeContainer({ replyNotifications, notifications })

  const view = render(
    <ContainerContext.Provider value={container}>
      <ReplyNotificationToggle />
    </ContainerContext.Provider>,
  )

  return { ...view, system, storage, notifications, replyNotifications }
}

describe('ReplyNotificationToggle (FR-MSG-032)', () => {
  it('is not offered when the browser has no Notifications API', () => {
    const { container } = renderToggle('unsupported')

    expect(container).toBeEmptyDOMElement()
  })

  it('shows as on when enabled and granted, and turns off persistently', async () => {
    const { storage } = renderToggle('granted')
    const button = screen.getByRole('button', { name: REPLY_NOTIFICATION_COPY.toggle.disable })

    expect(button).toHaveAttribute('aria-pressed', 'true')

    await userEvent.click(button)

    expect(screen.getByRole('button', { name: REPLY_NOTIFICATION_COPY.toggle.enable })).toHaveAttribute('aria-pressed', 'false')
    expect(storage.read(REPLY_NOTIFICATIONS_ENABLED_KEY)).toBe('false')
  })

  it('asks for permission when switched on, then confirms', async () => {
    const { system, notifications } = renderToggle('default')

    await userEvent.click(screen.getByRole('button', { name: REPLY_NOTIFICATION_COPY.toggle.enable }))

    expect(system.requests).toBe(1)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: REPLY_NOTIFICATION_COPY.toggle.disable })).toBeInTheDocument()
    })
    expect(notifications.messages()).toEqual([REPLY_NOTIFICATION_COPY.enabled.title])
  })

  it('explains how to unblock when the browser has blocked notifications', async () => {
    const { system, notifications } = renderToggle('denied', false)

    await userEvent.click(screen.getByRole('button', { name: REPLY_NOTIFICATION_COPY.toggle.enable }))

    expect(system.requests).toBe(0)
    await waitFor(() => {
      expect(notifications.notifications).toEqual([
        expect.objectContaining({ kind: 'warning', message: REPLY_NOTIFICATION_COPY.blocked.title }),
      ])
    })
    expect(screen.getByRole('button', { name: REPLY_NOTIFICATION_COPY.toggle.enable })).toHaveAttribute('aria-pressed', 'false')
  })

  it('follows a permission granted elsewhere once the store re-reads it', () => {
    const { system, replyNotifications } = renderToggle('default')

    system.current = 'granted'
    act(() => {
      replyNotifications.disable()
    })
    act(() => {
      void replyNotifications.enable()
    })

    expect(screen.getByRole('button', { name: REPLY_NOTIFICATION_COPY.toggle.disable })).toBeInTheDocument()
  })

  it('has no accessibility violations', async () => {
    const { container } = renderToggle('granted')

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})
