import type { SendResult, Unsubscribe } from '@/shared/ws/socket-client'

import type { OverrideKey, OverrideValue } from '@/domains/model-config/domain/override-key'

export type ConfigUpdatedEvent = {
  readonly activeOverrides: Readonly<Partial<Record<OverrideKey, OverrideValue>>>
  readonly appliedOverrides: Readonly<Partial<Record<OverrideKey, OverrideValue>>>
  readonly rejectedOverrides: readonly string[]
}

export interface ConfigurationGateway {
  update(overrides: Readonly<Partial<Record<OverrideKey, OverrideValue>>>): SendResult
  reset(): SendResult
  onUpdated(callback: (event: ConfigUpdatedEvent) => void): Unsubscribe
}
