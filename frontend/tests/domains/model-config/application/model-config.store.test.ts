import { describe, expect, it, vi } from 'vitest'

import type { ConnectionState, SendResult } from '@/shared/ws/socket-client'
import { ModelConfigStore } from '@/domains/model-config/application/model-config.store'
import type { ConfigUpdatedEvent, ConfigurationGateway } from '@/domains/model-config/application/ports'
import { toConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'

const profile = () => toConfigurationProfile(
  { model_id: 'claude', temperature: 0.7, max_tokens: 8192 },
  null,
  buildModelCatalog([], []),
)

const harness = () => {
  let updated: ((event: ConfigUpdatedEvent) => void) | undefined
  let connectionChanged: ((state: ConnectionState) => void) | undefined
  const gateway: ConfigurationGateway = {
    update: vi.fn<ConfigurationGateway['update']>(() => 'sent' satisfies SendResult),
    reset: vi.fn<ConfigurationGateway['reset']>(() => 'sent' satisfies SendResult),
    onUpdated(callback) {
      updated = callback
      return () => { updated = undefined }
    },
  }
  const store = new ModelConfigStore({
    profile: profile(),
    gateway,
    notifications: {
      success: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
      warning: vi.fn(),
    },
    connection: {
      state: { status: 'open', attempt: 0, nextRetryAt: null },
      onStateChange(callback) {
        connectionChanged = callback
        return () => { connectionChanged = undefined }
      },
    },
  })

  return {
    gateway,
    store,
    confirm: (event: ConfigUpdatedEvent) => updated?.(event),
    connection: (state: ConnectionState) => connectionChanged?.(state),
  }
}

describe('ModelConfigStore', () => {
  it('marks a proposal pending without changing the confirmed profile', () => {
    const { gateway, store } = harness()

    store.commit('temperature', 0.2)

    expect(gateway.update).toHaveBeenCalledWith({ temperature: 0.2 })
    expect(store.getSnapshot().profile.overrides).toEqual({})
    expect(store.getSnapshot().pendingKeys).toEqual(new Set(['temperature']))
  })

  it('replaces the full confirmed override set and clears the acknowledged proposal', () => {
    const { store, confirm } = harness()
    store.commit('temperature', 0.2)

    confirm({ activeOverrides: { temperature: 0.2 }, appliedOverrides: { temperature: 0.2 }, rejectedOverrides: [] })

    expect(store.getSnapshot().profile.overrides).toEqual({ temperature: 0.2 })
    expect(store.getSnapshot().pendingKeys.size).toBe(0)
  })

  it('keeps the confirmed value and exposes the server reason on rejection', () => {
    const { store, confirm } = harness()
    confirm({ activeOverrides: { max_tokens: 2048 }, appliedOverrides: {}, rejectedOverrides: [] })
    store.commit('max_tokens', 99999)

    confirm({
      activeOverrides: { max_tokens: 2048 },
      appliedOverrides: {},
      rejectedOverrides: ["'max_tokens' must be less than or equal to 4096"],
    })

    expect(store.getSnapshot()).toMatchObject({
      profile: { overrides: { max_tokens: 2048 } },
      rejectionReasons: ["'max_tokens' must be less than or equal to 4096"],
    })
    expect(store.getSnapshot().pendingKeys.size).toBe(0)
  })

  it('waits for reset confirmation before clearing confirmed overrides', () => {
    const { gateway, store, confirm } = harness()
    confirm({ activeOverrides: { temperature: 0.2 }, appliedOverrides: {}, rejectedOverrides: [] })

    store.reset()

    expect(gateway.reset).toHaveBeenCalledOnce()
    expect(store.getSnapshot().profile.overrides).toEqual({ temperature: 0.2 })
    expect(store.getSnapshot().resetPending).toBe(true)

    confirm({ activeOverrides: {}, appliedOverrides: {}, rejectedOverrides: [] })
    expect(store.getSnapshot().profile.overrides).toEqual({})
    expect(store.getSnapshot().resetPending).toBe(false)
  })

  it('resolves every stale proposal from authoritative state after reconnect', () => {
    const { store, confirm, connection } = harness()
    store.commit('temperature', 0.2)
    store.commit('max_tokens', 4096)
    connection({ status: 'reconnecting', attempt: 1, nextRetryAt: null })

    confirm({ activeOverrides: { temperature: 0.7 }, appliedOverrides: {}, rejectedOverrides: [] })

    expect(store.getSnapshot().profile.overrides).toEqual({ temperature: 0.7 })
    expect(store.getSnapshot().pendingKeys.size).toBe(0)
  })

  it('does not mark a dropped proposal pending', () => {
    const { gateway, store } = harness()
    vi.mocked(gateway.update).mockReturnValue('dropped-closed')
    store.commit('temperature', 0.2)
    expect(store.getSnapshot().pendingKeys.size).toBe(0)
  })

  it('sends a dependent max_tokens clamp only after the model is confirmed', () => {
    const { gateway, confirm } = harness()
    const cappedProfile = toConfigurationProfile(
      { model_id: 'large', max_tokens: 8192 },
      null,
      buildModelCatalog([
        { id: 'large', name: 'Large', provider: 'test', supportsTemperature: true, maxOutputTokens: 8192 },
        { id: 'small', name: 'Small', provider: 'test', supportsTemperature: true, maxOutputTokens: 4096 },
      ], []),
    )
    const cappedStore = new ModelConfigStore({
      profile: cappedProfile,
      gateway,
      notifications: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
      connection: {
        state: { status: 'open', attempt: 0, nextRetryAt: null },
        onStateChange: () => () => undefined,
      },
    })

    cappedStore.commit('model_id', 'small')
    expect(gateway.update).toHaveBeenCalledTimes(1)

    confirm({ activeOverrides: { model_id: 'small' }, appliedOverrides: { model_id: 'small' }, rejectedOverrides: [] })
    expect(gateway.update).toHaveBeenLastCalledWith({ max_tokens: 4096 })
    expect(cappedStore.getSnapshot().pendingKeys).toEqual(new Set(['max_tokens']))
  })
})