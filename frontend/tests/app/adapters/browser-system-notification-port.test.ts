import { describe, expect, it, jest } from '@jest/globals'

import {
  BrowserSystemNotificationPort,
  type NotificationApi,
} from '@/app/adapters/browser-system-notification-port'

type Created = { title: string; options: NotificationOptions | undefined; instance: FakeNotification }

class FakeNotification {
  onclick: (() => void) | null = null
  close = jest.fn()
}

const fakeApi = (
  permission: NotificationPermission,
  request: NotificationApi['requestPermission'] = () => Promise.resolve(permission),
  construct: () => void = () => {},
) => {
  const created: Created[] = []
  const api = function (this: unknown, title: string, options?: NotificationOptions) {
    construct()
    const instance = new FakeNotification()
    created.push({ title, options, instance })
    return instance
  } as unknown as NotificationApi
  Object.assign(api, { permission, requestPermission: request })

  return { api, created }
}

describe('BrowserSystemNotificationPort (FR-MSG-032)', () => {
  it('reports unsupported, and does nothing, without a Notifications API', async () => {
    const port = new BrowserSystemNotificationPort({ api: undefined })

    expect(port.permission()).toBe('unsupported')
    await expect(port.requestPermission()).resolves.toBe('unsupported')
    expect(() => {
      port.show({ title: 't', body: 'b', tag: 'x', onClick: () => {} })
    }).not.toThrow()
  })

  it.each<NotificationPermission>(['granted', 'denied', 'default'])('passes through %s', (permission) => {
    expect(new BrowserSystemNotificationPort({ api: fakeApi(permission).api }).permission()).toBe(permission)
  })

  it('resolves the promise form of requestPermission', async () => {
    const { api } = fakeApi('default', () => Promise.resolve('granted'))

    await expect(new BrowserSystemNotificationPort({ api }).requestPermission()).resolves.toBe('granted')
  })

  it('supports the legacy callback-only form', async () => {
    const { api } = fakeApi('default', (callback) => {
      callback?.('denied')
      return undefined
    })

    await expect(new BrowserSystemNotificationPort({ api }).requestPermission()).resolves.toBe('denied')
  })

  it('falls back to the current permission when the request rejects or throws', async () => {
    const rejecting = fakeApi('default', () => Promise.reject(new Error('blocked')))
    const throwing = fakeApi('denied', () => {
      throw new Error('no gesture')
    })

    await expect(new BrowserSystemNotificationPort({ api: rejecting.api }).requestPermission()).resolves.toBe('default')
    await expect(new BrowserSystemNotificationPort({ api: throwing.api }).requestPermission()).resolves.toBe('denied')
  })

  it('shows a notification only when granted', () => {
    const denied = fakeApi('denied')
    new BrowserSystemNotificationPort({ api: denied.api }).show({ title: 't', body: 'b', tag: 'x', onClick: () => {} })

    expect(denied.created).toEqual([])
  })

  it('shows title, body, tag and icon, and on click focuses, closes and calls back', () => {
    const { api, created } = fakeApi('granted')
    const focusWindow = jest.fn()
    const onClick = jest.fn()
    const port = new BrowserSystemNotificationPort({ api, focusWindow, iconUrl: () => 'https://app.test/favicon.svg' })

    port.show({ title: 'Job 42', body: 'Done.', tag: 'reply:c-1', onClick })

    expect(created[0]).toMatchObject({
      title: 'Job 42',
      options: { body: 'Done.', tag: 'reply:c-1', icon: 'https://app.test/favicon.svg' },
    })

    created[0]?.instance.onclick?.()

    expect(focusWindow).toHaveBeenCalled()
    expect(created[0]?.instance.close).toHaveBeenCalled()
    expect(onClick).toHaveBeenCalled()
  })

  it('swallows a constructor that throws (Android Chrome outside a service worker)', () => {
    const { api } = fakeApi('granted', undefined, () => {
      throw new TypeError('Illegal constructor')
    })
    const port = new BrowserSystemNotificationPort({ api, iconUrl: () => undefined })

    expect(() => {
      port.show({ title: 't', body: 'b', tag: 'x', onClick: () => {} })
    }).not.toThrow()
  })

  it('treats a node environment with no Notification global as unsupported', () => {
    expect(new BrowserSystemNotificationPort().permission()).toBe('unsupported')
  })
})
