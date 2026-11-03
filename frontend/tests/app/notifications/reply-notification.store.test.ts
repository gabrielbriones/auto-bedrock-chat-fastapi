import { describe, expect, it, jest } from '@jest/globals'

import { conversationId, type ConversationId } from '@/shared/kernel/branded'
import { FixedClock, Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'
import type { SystemNotificationPermission } from '@/shared/ports/system-notification-port'
import type { ConnectionState } from '@/shared/ws/socket-client'

import {
  REPLY_NOTIFICATIONS_ENABLED_KEY,
  REPLY_NOTIFICATIONS_PROMPTED_KEY,
  ReplyNotificationStore,
} from '@/app/notifications/reply-notification.store'
import { ChatSessionStore } from '@/domains/messaging/application/chat-session.store'
import type { MessagingEvent } from '@/domains/messaging/application/ports'
import {
  FakePageAttentionPort,
  FakeSystemNotificationPort,
  InMemoryPreferenceStorage,
} from '../../shared/ports/fake-reply-notification-ports'

const OPEN: ConnectionState = { status: 'open', attempt: 0, nextRetryAt: null }

const fixedInstant = () => {
  const instant = Instant.fromIso('2026-10-30T10:00:00Z')
  if (!isOk(instant)) throw new Error('unparseable fixture instant')
  return instant.value
}

const answered = (text: string, id: string | null = 'c-1'): MessagingEvent => ({
  kind: 'answered',
  text,
  messageId: 'm-1',
  conversationId: id,
  toolCalls: [],
  toolResults: [],
  citations: [],
  truncated: false,
  configuredModel: { id: 'model-1', name: 'Model 1' },
})

type HarnessOptions = {
  readonly permission?: SystemNotificationPermission
  readonly attended?: boolean
  readonly stored?: Readonly<Record<string, string>>
}

const createHarness = ({ permission = 'granted', attended = false, stored = {} }: HarnessOptions = {}) => {
  let emitEvent: ((event: MessagingEvent) => void) | undefined
  const chatSession = new ChatSessionStore({
    clock: new FixedClock(fixedInstant()),
    logger: { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() },
    gateway: {
      sendChat: () => 'sent',
      onEvent: (callback) => {
        emitEvent = callback
        return () => {}
      },
    },
    connection: { state: OPEN, onStateChange: () => () => {} },
  })
  const system = new FakeSystemNotificationPort(permission)
  const attention = new FakePageAttentionPort(attended)
  const storage = new InMemoryPreferenceStorage()
  for (const [key, value] of Object.entries(stored)) storage.write(key, value)

  const describeReply = jest.fn((reply: { conversationId: ConversationId | null }) => ({
    conversationId: reply.conversationId ?? conversationId('c-active'),
    title: 'Job 42 analysis',
  }))
  const store = new ReplyNotificationStore({ chatSession, system, attention, storage, describe: describeReply })

  return {
    store,
    system,
    attention,
    storage,
    chatSession,
    describeReply,
    reply(text = 'Here is the analysis.', id: string | null = 'c-1') {
      chatSession.send('analyse job 42')
      emitEvent?.(answered(text, id))
    },
    fail(text: string) {
      chatSession.send('analyse job 42')
      emitEvent?.({ kind: 'failed', text })
    },
  }
}

const flush = () => new Promise<void>((resolve) => { setTimeout(resolve, 0) })

describe('ReplyNotificationStore (FR-MSG-032)', () => {
  describe('when to notify', () => {
    it('notifies with the conversation title and a reply preview while the tab is in the background', () => {
      const { system, reply } = createHarness()

      reply('**Bottleneck:** `extract_job_metrics` dominates CPU time.')

      expect(system.shown).toEqual([
        expect.objectContaining({
          title: 'Job 42 analysis',
          body: 'Bottleneck: extract_job_metrics dominates CPU time.',
          tag: 'reply:c-1',
        }),
      ])
    })

    it('does not notify while the tab is focused and visible', () => {
      const { system, store, reply } = createHarness({ attended: true })

      reply()

      expect(system.shown).toEqual([])
      expect(store.getSnapshot().unread).toBe(0)
    })

    it('does not notify when the user turned notifications off', () => {
      const { system, store, reply } = createHarness({ stored: { [REPLY_NOTIFICATIONS_ENABLED_KEY]: 'false' } })

      reply()

      expect(store.getSnapshot()).toMatchObject({ enabled: false, active: false })
      expect(system.shown).toEqual([])
    })

    it.each<SystemNotificationPermission>(['denied', 'default', 'unsupported'])(
      'does not notify, count unread or throw when permission is %s',
      (permission) => {
        const { system, store, reply } = createHarness({ permission })
        // A dismissed or blocked prompt leaves the permission where it was.
        system.answer = permission

        expect(() => { reply() }).not.toThrow()
        expect(system.shown).toEqual([])
        expect(store.getSnapshot()).toMatchObject({ active: false, unread: 0 })
      },
    )

    it('reports an unsupported browser so the control can be hidden', () => {
      const { store } = createHarness({ permission: 'unsupported' })

      expect(store.getSnapshot().supported).toBe(false)
    })

    it('notifies on a failed reply, saying so', () => {
      const { system, fail } = createHarness()

      fail('Model unavailable')

      expect(system.shown[0]?.body).toBe('Reply failed: Model unavailable')
    })

    it('falls back to generic text for an empty reply', () => {
      const { system, reply } = createHarness()

      reply('   ')

      expect(system.shown[0]?.body).toBe('The assistant replied.')
    })

    it('resolves a reply without a conversation id through the composition root', () => {
      const { system, describeReply, reply } = createHarness()

      reply('Done.', null)

      expect(describeReply).toHaveBeenCalledWith(expect.objectContaining({ conversationId: null }))
      expect(system.shown[0]?.tag).toBe('reply:c-active')
    })

    it('picks up a permission the user changed in browser settings', () => {
      const { system, store, reply } = createHarness({ permission: 'denied' })
      system.current = 'granted'

      reply()

      expect(store.getSnapshot().active).toBe(true)
      expect(system.shown).toHaveLength(1)
    })

    it('stops listening once disposed', () => {
      const { system, store, reply } = createHarness()
      store.dispose()

      reply()

      expect(system.shown).toEqual([])
    })
  })

  describe('unread indicator', () => {
    it('counts replies while away and clears when the user returns', () => {
      const { store, attention, reply } = createHarness()

      reply()
      reply()
      expect(store.getSnapshot().unread).toBe(2)

      attention.set(true)
      expect(store.getSnapshot().unread).toBe(0)
    })
  })

  describe('clicking a notification', () => {
    it('reports the conversation that received the reply and clears unread', () => {
      const { system, store, reply } = createHarness()
      const activate = jest.fn()
      store.onActivate(activate)

      reply('Done.', 'c-9')
      system.shown[0]?.onClick()

      expect(activate).toHaveBeenCalledWith('c-9')
      expect(store.getSnapshot().unread).toBe(0)
    })

    it('stops reporting after unsubscribe', () => {
      const { system, store, reply } = createHarness()
      const activate = jest.fn()
      store.onActivate(activate)()

      reply()
      system.shown[0]?.onClick()

      expect(activate).not.toHaveBeenCalled()
    })
  })

  describe('asking for permission', () => {
    it('never asks on load', () => {
      const { system } = createHarness({ permission: 'default' })

      expect(system.requests).toBe(0)
    })

    it('asks once on the first user send and remembers that it asked', async () => {
      const { system, store, storage, reply } = createHarness({ permission: 'default' })

      store.userSent()
      reply()
      await flush()
      store.userSent()
      reply()

      expect(system.requests).toBe(1)
      expect(storage.read(REPLY_NOTIFICATIONS_PROMPTED_KEY)).toBe('true')
      expect(store.getSnapshot().active).toBe(true)
    })

    it('does not ask for a send the user did not make, such as a deep-link autosend', () => {
      const { system, store, storage, chatSession } = createHarness({ permission: 'default' })

      chatSession.send('autosent preset')

      expect(system.requests).toBe(0)
      expect(storage.read(REPLY_NOTIFICATIONS_PROMPTED_KEY)).toBeNull()

      store.userSent()
      expect(system.requests).toBe(1)
    })

    it('asks only once even when storage cannot remember it', async () => {
      const { system, store, storage } = createHarness({ permission: 'default' })
      // A dismissed prompt leaves the permission undecided.
      system.answer = 'default'
      storage.write = () => {}

      store.userSent()
      await flush()
      store.userSent()

      expect(system.requests).toBe(1)
    })

    it('does not ask on send if it already asked in an earlier visit', () => {
      const { system, store } = createHarness({
        permission: 'default',
        stored: { [REPLY_NOTIFICATIONS_PROMPTED_KEY]: 'true' },
      })

      store.userSent()

      expect(system.requests).toBe(0)
    })

    it('does not ask on send when the user turned notifications off', () => {
      const { system, store } = createHarness({
        permission: 'default',
        stored: { [REPLY_NOTIFICATIONS_ENABLED_KEY]: 'false' },
      })

      store.userSent()

      expect(system.requests).toBe(0)
    })
  })

  describe('the on/off preference', () => {
    it('asks for permission when switched on and persists the choice', async () => {
      const { system, store, storage } = createHarness({
        permission: 'default',
        stored: { [REPLY_NOTIFICATIONS_ENABLED_KEY]: 'false' },
      })

      await expect(store.enable()).resolves.toBe('granted')

      expect(system.requests).toBe(1)
      expect(storage.read(REPLY_NOTIFICATIONS_ENABLED_KEY)).toBe('true')
      expect(store.getSnapshot()).toMatchObject({ enabled: true, active: true })
    })

    it('stays inactive when the user blocks the prompt', async () => {
      const { system, store } = createHarness({ permission: 'default' })
      system.answer = 'denied'

      await expect(store.enable()).resolves.toBe('denied')

      expect(store.getSnapshot()).toMatchObject({ enabled: true, active: false, permission: 'denied' })
    })

    it('does not prompt again when permission is already decided', async () => {
      const { system, store } = createHarness({ permission: 'denied' })

      await store.enable()

      expect(system.requests).toBe(0)
    })

    it('persists switching off and survives a reload', () => {
      const first = createHarness()
      first.store.disable()

      expect(first.storage.read(REPLY_NOTIFICATIONS_ENABLED_KEY)).toBe('false')

      const reloaded = createHarness({ stored: Object.fromEntries(first.storage.values) })
      expect(reloaded.store.getSnapshot()).toMatchObject({ enabled: false, active: false })
    })

    it('notifies subscribers of changes', () => {
      const { store } = createHarness()
      const listener = jest.fn()
      store.subscribe(listener)

      store.disable()

      expect(listener).toHaveBeenCalled()
    })
  })
})
