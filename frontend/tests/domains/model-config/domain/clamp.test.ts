import { describe, expect, it } from 'vitest'

import { clampMaxTokens, maxTokensCap, wasClamped } from '@/domains/model-config/domain/clamp'
import type { ModelDescriptor } from '@/domains/model-config/domain/model-descriptor'

const model = (maxOutputTokens: number): ModelDescriptor => ({
  id: 'm',
  name: 'M',
  provider: 'p',
  supportsTemperature: true,
  maxOutputTokens,
})

describe('maxTokensCap', () => {
  it('follows the effective model cap', () => {
    expect(maxTokensCap(model(4096))).toBe(4096)
  })

  it('falls back to the field maximum with no resolved model', () => {
    expect(maxTokensCap(null)).toBe(100_000)
  })
})

describe('clampMaxTokens', () => {
  it('is a no-op under the cap', () => {
    expect(clampMaxTokens(2048, model(4096))).toBe(2048)
  })

  it('clamps a value above the newly selected model cap (FR-CFG-005)', () => {
    expect(clampMaxTokens(8192, model(4096))).toBe(4096)
  })

  it('clamps below the field minimum', () => {
    expect(clampMaxTokens(0, model(4096))).toBe(1)
  })
})

describe('wasClamped', () => {
  it('is false when the value already fits', () => {
    expect(wasClamped(2048, model(4096))).toBe(false)
  })

  it('is true when the clamp actually changed the value (FR-CFG-018)', () => {
    expect(wasClamped(8192, model(4096))).toBe(true)
  })
})
