import type { OverrideValue } from '@/domains/model-config/domain/override-key'

const FLOAT_TOLERANCE = 1e-9

// DESIGN-001 §5.2 domain service. Boolean coercion first (so e.g. `1`/`true` agree), then a float
// tolerance for numbers, otherwise strict equality — preserves legacy Q10 (moved and moved back
// shows no badge) and the Q10 float-boundary case.
export const valueEqualsDefault = (value: OverrideValue, defaultValue: OverrideValue): boolean => {
  if (typeof value === 'boolean' || typeof defaultValue === 'boolean') {
    return Boolean(value) === Boolean(defaultValue)
  }

  if (typeof value === 'number' && typeof defaultValue === 'number') {
    return Math.abs(value - defaultValue) < FLOAT_TOLERANCE
  }

  return value === defaultValue
}
